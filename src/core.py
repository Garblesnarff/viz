from __future__ import annotations
'''
   Home to the Core class which tracks program state. Used by GUI & commandline
   to create a list of components and create a video thread to export.
'''
from PyQt5 import QtCore, QtGui, uic
import sys
import os
import json
from importlib import import_module
import logging
from typing import List, Tuple, Dict, Any, Optional, TYPE_CHECKING
from . import toolkit

if TYPE_CHECKING:
    from .component import Component
    from .video_thread import Worker

log = logging.getLogger('AVP.Core')
STDOUT_LOGLVL = logging.WARNING
FILE_LIBLOGLVL = logging.WARNING
FILE_LOGLVL = logging.INFO


class Core:
    '''
        MainWindow and Command module both use an instance of this class
        to store the core program state. This object tracks the components,
        talks to the components, handles opening/creating project files
        and presets, and creates the video thread to export.
        This class also stores constants as class variables.
    '''

    # Class-level variables (static) will be populated by storeSettings
    dataDir: str
    presetDir: str
    componentsPath: str
    junkStream: str
    encoderOptions: Dict[str, Any]
    resolutions: List[str]
    logDir: str
    logEnabled: bool
    previewEnabled: bool
    FFMPEG_BIN: str
    settings: QtCore.QSettings
    videoFormats: List[str]
    audioFormats: List[str]
    imageFormats: List[str]
    canceled: bool
    mode: str

    def __init__(self) -> None:
        self.importComponents()
        self.selectedComponents: List[Component] = []
        self.savedPresets: Dict[str, Dict[str, Any]] = {}  # copies of presets to detect modification
        self.openingProject: bool = False
        self.videoThread: Optional[QtCore.QThread] = None #to avoid type: ignore

    def __repr__(self) -> str:
        return "\n=~=~=~=\n".join(
            [repr(comp) for comp in self.selectedComponents]
        )

    def importComponents(self) -> None:
        def findComponents() -> List[str]:
            return [
                os.path.splitext(f)[0]
                for f in os.listdir(Core.componentsPath)
                if not f.startswith("__") and os.path.splitext(f)[1] == '.py'
            ]

        log.debug('Importing component modules')
        self.modules = [
            import_module('.components.%s' % name, __package__)
            for name in findComponents()
        ]
        # store canonical module names and indexes
        self.moduleIndexes: List[int] = list(range(len(self.modules)))
        self.compNames: List[str] = [mod.Component.name for mod in self.modules]
        # alphabetize modules by Component name
        sortedModules = sorted(zip(self.compNames, self.modules))
        self.compNames = [y[0] for y in sortedModules]
        self.modules = [y[1] for y in sortedModules]

        # store alternative names for modules
        self.altCompNames: List[Tuple[str, int]] = []
        for i, mod in enumerate(self.modules):
            if hasattr(mod.Component, 'names'):
                for name in mod.Component.names():
                    self.altCompNames.append((name, i))

    def componentListChanged(self) -> None:
        for i, component in enumerate(self.selectedComponents):
            component.compPos = i

    def insertComponent(self, compPos: int, component: Any, loader: Any) -> int:
        '''
            Creates a new component using these args:
            (compPos, component obj or moduleIndex, MWindow/Command/Core obj)
        '''
        if compPos < 0 or compPos > len(self.selectedComponents):
            compPos = len(self.selectedComponents)
        if len(self.selectedComponents) > 50:
            return -1

        if isinstance(component, int):
            # create component using module index in self.modules
            moduleIndex: int = int(component)
            log.debug(
                'Creating new component from module #%s', str(moduleIndex))
            component = self.modules[moduleIndex].Component(
                moduleIndex, compPos, self
            )
            component.widget(loader)
        else:
            #Inserting previously created component
            moduleIndex = -1
            log.debug(
                'Inserting previously-created %s component', component.name)


        component._error.connect( #type: ignore
            loader.videoThreadError
        )
        self.selectedComponents.insert(
            compPos,
            component
        )
        if hasattr(loader, 'insertComponent'):
            loader.insertComponent(compPos)

        self.componentListChanged()
        self.updateComponent(compPos)
        return compPos

    def moveComponent(self, startI: int, endI: int) -> int:
        comp = self.selectedComponents.pop(startI)
        self.selectedComponents.insert(endI, comp)

        self.componentListChanged()
        return endI

    def removeComponent(self, i: int) -> None:
        self.selectedComponents.pop(i)
        self.componentListChanged()

    def clearComponents(self) -> None:
        self.selectedComponents = list()
        self.componentListChanged()

    def updateComponent(self, i: int) -> None:
        log.debug(
            'Auto-updating %s #%s',
            self.selectedComponents[i], str(i))
        self.selectedComponents[i].update(auto=True)

    def moduleIndexFor(self, compName: str) -> Optional[int]:
        try:
            index = self.compNames.index(compName)
            return self.moduleIndexes[index]
        except ValueError:
            for altName, modI in self.altCompNames:
                if altName == compName:
                    return self.moduleIndexes[modI]
            return None

    def clearPreset(self, compIndex: int) -> None:
        self.selectedComponents[compIndex].currentPreset = None

    def openPreset(self, filepath: str, compIndex: int, presetName: str) -> bool:
        '''Applies a preset to a specific component'''
        saveValueStore = self.getPreset(filepath)
        if not saveValueStore:
            return False
        comp = self.selectedComponents[compIndex]
        comp.loadPreset(
            saveValueStore,
            presetName
        )

        self.savedPresets[presetName] = dict(saveValueStore)
        return True

    def getPreset(self, filepath: str) -> Optional[Dict[str, Any]]:
        '''Returns the preset dict stored at this filepath'''
        if not os.path.exists(filepath):
            return None
        with open(filepath, 'r') as f:
            for line in f:
                saveValueStore = toolkit.presetFromString(line.strip())
                break  # Only read the first line
        return saveValueStore

    def getPresetDir(self, comp: Component) -> str:
        '''Get the preset subdir for a particular version of a component'''
        return os.path.join(Core.presetDir, comp.name, str(comp.version))

    def openProject(self, loader: Any, filepath: str) -> Optional[bool]:
        ''' loader is the object calling this method which must have
        its own showMessage(**kwargs) method for displaying errors.
        '''
        if not os.path.exists(filepath):
            loader.showMessage(msg='Project file not found.')
            return None

        errcode, data = self.parseAvFile(filepath)
        if errcode == 0:
            self.openingProject = True
            try:
                if hasattr(loader, 'window'):
                    for widget, value in data['WindowFields']:
                        widget = eval('loader.%s' % widget)
                        with toolkit.blockSignals(widget):
                            toolkit.setWidgetValue(widget, value)

                for key, value in data['Settings']:
                    Core.settings.setValue(key, value)
                for tup in data['Components']:
                    name, vers, preset = tup
                    clearThis = False
                    modified = False

                    # add loaded named presets to savedPresets dict
                    if 'preset' in preset and preset['preset'] is not None:
                        nam = preset['preset']
                        filepath2 = os.path.join(
                            Core.presetDir, name, str(vers), nam)
                        origSaveValueStore = self.getPreset(filepath2)
                        if origSaveValueStore:
                            self.savedPresets[nam] = dict(origSaveValueStore)
                            modified = not origSaveValueStore == preset
                        else:
                            # saved preset was renamed or deleted
                            clearThis = True

                    # create the actual component object & get its index
                    i = self.insertComponent(
                        -1,
                        self.moduleIndexFor(name), #type: ignore
                        loader
                    )
                    if i == -1:
                        loader.showMessage(msg="Too many components!")
                        break

                    try:
                        if 'preset' in preset and preset['preset'] is not None:
                            self.selectedComponents[i].loadPreset(
                                preset
                            )
                        else:
                            self.selectedComponents[i].loadPreset(
                                preset,
                                preset['preset']
                            )
                    except KeyError as e:
                        log.warning('%s missing value: %s' % (
                            self.selectedComponents[i], e)
                        )

                    if clearThis:
                        self.clearPreset(i)
                    if hasattr(loader, 'updateComponentTitle'):
                        loader.updateComponentTitle(i, modified)
                self.openingProject = False
                return True
            except Exception:
                errcode = 1
                data = sys.exc_info()

        if errcode == 1:
            typ, value, tb = data
            if typ.__name__ == 'KeyError':
                # probably just an old version, still loadable
                log.warning('Project file missing value: %s' % value)
                return None
            if hasattr(loader, 'createNewProject'):
                loader.createNewProject(prompt=False)
            msg = '%s: %s\n\n' % (typ.__name__, value)
            msg += toolkit.formatTraceback(tb)
            loader.showMessage(
                msg="Project file '%s' is corrupted." % filepath,
                showCancel=False,
                icon='Warning',
                detail=msg)
            self.openingProject = False
            return False
        return None

    def parseAvFile(self, filepath: str) -> Tuple[int, Dict[str, Any]]:
        '''
            Parses an avp (project) or avl (preset package) file.
            Returns dictionary with section names as the keys, each one
            contains a list of tuples: (compName, version, compPresetDict)
        '''
        log.debug('Parsing av file: %s', filepath)
        validSections = (
                    'Components',
                    'Settings',
                    'WindowFields'
                )
        data: Dict[str, Any] = {sect: [] for sect in validSections}
        try:
            with open(filepath, 'r') as f:
                def parseLine(line: str) -> Tuple[str, str]:
                    '''Decides if a file line is a section header'''
                    line = line.strip()
                    newSection = ''

                    if line.startswith('[') and line.endswith(']') \
                            and line[1:-1] in validSections:
                        newSection = line[1:-1]

                    return line, newSection

                section = ''
                i = 0
                for line in f:
                    line, newSection = parseLine(line)
                    if newSection:
                        section = str(newSection)
                        continue
                    if line and section == 'Components':
                        if i == 0:
                            lastCompName = str(line)
                            i += 1
                        elif i == 1:
                            lastCompVers = str(line)
                            i += 1
                        elif i == 2:
                            lastCompPreset = toolkit.presetFromString(line)
                            data[section].append((
                                lastCompName,
                                lastCompVers,
                                lastCompPreset
                            ))
                            i = 0
                    elif line and section:
                        key, value = line.split('=', 1)
                        data[section].append((key, value.strip()))

            return 0, data
        except Exception:
            return 1, sys.exc_info()

    def importPreset(self, filepath: str) -> Tuple[bool, str]:
        errcode, data = self.parseAvFile(filepath)
        returnList = []
        if errcode == 0:
            name, vers, preset = data['Components'][0]
            presetName = preset['preset'] \
                if preset['preset'] else os.path.basename(filepath)[:-4]
            newPath = os.path.join(
                Core.presetDir,
                name,
                vers,
                presetName
            )
            if os.path.exists(newPath):
                return False, newPath
            preset['preset'] = presetName
            self.createPresetFile(
                name, vers, presetName, preset
            )
            return True, presetName
        elif errcode == 1:
            # TODO: an error message
            return False, ''
        return False, '' #added to make the linter happy

    def exportPreset(self, exportPath: str, compName: str, vers: str, origName: str) -> Optional[bool]:
        internalPath = os.path.join(
            Core.presetDir, compName, str(vers), origName
        )
        if not os.path.exists(internalPath):
            return None
        if os.path.exists(exportPath):
            os.remove(exportPath)
        with open(internalPath, 'r') as f:
            internalData = [line for line in f]
        try:
            saveValueStore = toolkit.presetFromString(internalData[0].strip())
            self.createPresetFile(
                compName, vers,
                origName, saveValueStore,
                exportPath
            )
            return True
        except Exception:
            return False

    def createPresetFile(
            self, compName: str, vers: str, presetName: str, saveValueStore: Dict[str, Any], filepath: str = '') -> None:
        '''Create a preset file (.avl) at filepath using args.
        Or if filepath is empty, create an internal preset using args'''
        if not filepath:
            dirname = os.path.join(Core.presetDir, compName, str(vers))
            if not os.path.exists(dirname):
                os.makedirs(dirname)
            filepath = os.path.join(dirname, presetName)
            internal = True
        else:
            if not filepath.endswith('.avl'):
                filepath += '.avl'
            internal = False

        with open(filepath, 'w') as f:
            if not internal:
                f.write('[Components]\n')
                f.write('%s\n' % compName)
                f.write('%s\n' % str(vers))
            f.write('%s\n' % toolkit.presetToString(saveValueStore))

    def newVideoWorker(self, loader: Any, audioFile: str, outputPath: str) -> 'Worker':
        '''loader is MainWindow or Command object which must own the thread'''
        from . import video_thread
        self.videoThread = QtCore.QThread(loader)
        videoWorker = video_thread.Worker(
            loader, audioFile, outputPath, self.selectedComponents
        )
        videoWorker.moveToThread(self.videoThread)
        videoWorker.videoCreated.connect(self.stopVideoThread)

        self.videoThread.start()
        return videoWorker

    def stopVideoThread(self) -> None:
        if self.videoThread:
            self.videoThread.quit()
            self.videoThread.wait()

    def cancel(self) -> None:
        Core.canceled = True

    def reset(self) -> None:
        Core.canceled = False

    @classmethod
    def storeSettings(cls) -> None:
        '''Store settings/paths to directories as class variables'''
        from .__init__ import wd
        from .toolkit.ffmpeg import findFfmpeg

        cls.wd = wd
        dataDir = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.AppConfigLocation
        )
        # Windows: C:/Users/<USER>/AppData/Local/audio-visualizer
        # macOS: ~/Library/Preferences/audio-visualizer
        # Linux: ~/.config/audio-visualizer
        with open(os.path.join(wd, 'encoder-options.json')) as json_file:
            encoderOptions = json.load(json_file)

        # Locate FFmpeg
        ffmpegBin = findFfmpeg()
        if not ffmpegBin:
            print("Could not find FFmpeg")

        settings = {
            'canceled': False,
            'FFMPEG_BIN': ffmpegBin,
            'dataDir': dataDir,
            'settings': QtCore.QSettings(
                            os.path.join(dataDir, 'settings.ini'),
                            QtCore.QSettings.IniFormat),
            'presetDir': os.path.join(dataDir, 'presets'),
            'componentsPath': os.path.join(wd, 'components'),
            'junkStream': os.path.join(wd, 'gui', 'background.png'),
            'encoderOptions': encoderOptions,
            'resolutions': [
                '1920x1080',
                '1280x720',
                '854x480',
            ],
            'logDir': os.path.join(dataDir, 'log'),
            'logEnabled': False,
            'previewEnabled': True,
        }

        settings['videoFormats'] = toolkit.appendUppercase([
            '*.mp4',
            '*.mov',
            '*.mkv',
            '*.avi',
            '*.webm',
            '*.flv',
        ])
        settings['audioFormats'] = toolkit.appendUppercase([
            '*.mp3',
            '*.wav',
            '*.ogg',
            '*.fla',
            '*.flac',
            '*.aac',
        ])
        settings['imageFormats'] = toolkit.appendUppercase([
            '*.png',
            '*.jpg',
            '*.tif',
            '*.tiff',
            '*.gif',
            '*.bmp',
            '*.ico',
            '*.xbm',
            '*.xpm',
        ])

        # Register all settings as class variables
        for classvar, val in settings.items():
            setattr(cls, classvar, val)

        cls.loadDefaultSettings()
        if not os.path.exists(cls.dataDir):
            os.makedirs(cls.dataDir)
        for neededDirectory in (
          cls.presetDir, cls.logDir, cls.settings.value("projectDir")):
            if neededDirectory and not os.path.exists(neededDirectory):
                os.mkdir(neededDirectory)
        cls.makeLogger(deleteOldLogs=True)

    @classmethod
    def loadDefaultSettings(cls) -> None:
        # settings that get saved into the ini file
        cls.defaultSettings = {
            "outputWidth": 1280,
            "outputHeight": 720,
            "outputFrameRate": 30,
            "outputAudioCodec": "AAC",
            "outputAudioBitrate": "192",
            "outputVideoCodec": "H264",
            "outputVideoBitrate": "2500",
            "outputVideoFormat": "yuv420p",
            "outputPreset": "medium",
            "outputFormat": "mp4",
            "outputContainer": "MP4",
            "projectDir": os.path.join(cls.dataDir, 'projects'),
            "pref_insertCompAtTop": True,
            "pref_genericPreview": True,
            "pref_undoLimit": 10,
        }

        for parm, value in cls.defaultSettings.items():
            if cls.settings.value(parm) is None:
                cls.settings.setValue(parm, value)

        # Allow manual editing of prefs. (Surprisingly necessary as Qt seems to
        # store True as 'true' but interprets a manually-added 'true' as str.)
        for key in cls.settings.allKeys():
            if not key.startswith('pref_'):
                continue
            val = cls.settings.value(key)
            try:
                val = int(val)
            except ValueError:
                if val == 'true':
                    val = True
                elif val == 'false':
                    val = False
            cls.settings.setValue(key, val)

    @staticmethod
    def makeLogger(deleteOldLogs: bool = False) -> None:
        # send critical log messages to stdout
        logStream = logging.StreamHandler()
        logStream.setLevel(STDOUT_LOGLVL)
        streamFormatter = logging.Formatter(
            '<%(name)s> %(levelname)s: %(message)s'
        )
        logStream.setFormatter(streamFormatter)
        log = logging.getLogger('AVP')
        log.addHandler(logStream)

        if FILE_LOGLVL is not None:
            # write log files as well!
            Core.logEnabled = True
            logFilename = os.path.join(Core.logDir, 'avp_debug.log')
            libLogFilename = os.path.join(Core.logDir, 'global_debug.log')

            if deleteOldLogs:
                for log_ in (logFilename, libLogFilename):
                    if os.path.exists(log_):
                        os.remove(log_)

            logFile = logging.FileHandler(logFilename, delay=True)
            logFile.setLevel(FILE_LOGLVL)
            libLogFile = logging.FileHandler(libLogFilename, delay=True)
            libLogFile.setLevel(FILE_LIBLOGLVL)
            fileFormatter = logging.Formatter(
                '[%(asctime)s] %(threadName)-10.10s %(name)-23.23s %(levelname)s: '
                '%(message)s'
            )
            logFile.setFormatter(fileFormatter)
            libLogFile.setFormatter(fileFormatter)

            libLog = logging.getLogger()
            log.addHandler(logFile)
            libLog.addHandler(libLogFile)
            # lowest level must be explicitly set on the root Logger
            libLog.setLevel(0)

# always store settings in class variables even if a Core object is not created
Core.storeSettings()