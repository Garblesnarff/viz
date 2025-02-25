'''
    When using GUI mode, this module's object (the main window) takes
    user input to construct a program state (stored in the Core object).
    This shows a preview of the video being created and allows for saving
    projects and exporting the video at a later time.
'''
from PyQt5 import QtCore, QtWidgets, uic
import PyQt5.QtWidgets as QtWidgets
from PIL import Image
from queue import Queue
import sys
import os
import signal
import filecmp
import time
import logging
from typing import List, Optional, Any, Dict, Tuple, Union

from ..core import Core
from . import preview_thread
from .preview_win import PreviewWindow
from .presetmanager import PresetManager
from .actions import *  # We'll need this for type hinting the actions
from ..toolkit import (
    disableWhenEncoding, disableWhenOpeningProject, checkOutput, blockSignals
)


appName = 'Audio Visualizer'
log = logging.getLogger('AVP.Gui.MainWindow')


class MainWindow(QtWidgets.QMainWindow):
    '''
        The MainWindow wraps many Core methods in order to update the GUI
        accordingly.  E.g., instead of self.core.openProject(), it will use
        self.openProject() and update the window titlebar within the wrapper.

        MainWindow manages the autosave feature, although Core has the
        primary functions for opening and creating project files.
    '''

    createVideo = QtCore.pyqtSignal()
    newTask = QtCore.pyqtSignal(list)
    processTask = QtCore.pyqtSignal()

    def __init__(self, project: Optional[str]) -> None:
        super().__init__()
        log.debug(f'Main thread id: {int(QtCore.QThread.currentThreadId())}')
        uic.loadUi(os.path.join(Core.wd, "gui", "mainwindow.ui"), self)
        desk = QtWidgets.QDesktopWidget()
        dpi = desk.physicalDpiX()
        log.info(f"Detected screen DPI: {dpi}")

        self.resize_window_for_dpi(dpi)

        self.core = Core()
        Core.mode = 'GUI'
        self.pages: List[QtWidgets.QWidget] = []
        self.lastAutosave: float = time.time()
        self.autosaveTimes: List[float] = []
        self.autosaveCooldown: float = 0.2
        self.encoding: bool = False
        self.currentProject: Optional[str] = None

        self.dataDir: str = Core.dataDir
        self.presetDir: str = Core.presetDir
        self.autosavePath: str = os.path.join(self.dataDir, 'autosave.avp')
        self.settings: QtCore.QSettings = Core.settings

        self.undoStack: QtWidgets.QUndoStack = QtWidgets.QUndoStack(self)
        self.undoStack.setUndoLimit(int(self.settings.value("pref_undoLimit", 10)))
        self.undoStack.undo = disableWhenEncoding(self.undoStack.undo)  # type: ignore
        self.undoStack.redo = disableWhenEncoding(self.undoStack.redo)  # type: ignore

        self.undoDialog = self._setup_undo_dialog()
        self.presetManager = PresetManager(self)
        self.previewWindow = self._setup_preview_window()
        self.previewQueue, self.previewThread, self.previewWorker = self._setup_preview_thread()
        self.timer = self._setup_preview_timer()
        self._setup_ui_elements()


        if project and project != self.autosavePath:
            self._load_project_on_startup(project)
        else:
            self._restore_last_project()

        self.drawPreview(True)
        self._verify_ffmpeg()

    def _setup_ui_elements(self) -> None:
        """Sets up UI elements like buttons, menus, and hotkeys."""
        self._setup_undo_redo_ui()
        self._setup_component_list_ui()
        self._setup_export_ui()
        self._setup_encoder_settings_ui()
        self._setup_projects_menu()
        self._setup_presets_button()
        self.updateWindowTitle()

    def _setup_undo_redo_ui(self) -> None:
        """Configures Undo/Redo UI elements and hotkeys."""
        style = self.pushButton_undo.style()
        undoButton = self.pushButton_undo
        undoButton.setIcon(style.standardIcon(QtWidgets.QStyle.SP_FileDialogBack))
        undoButton.clicked.connect(self.undoStack.undo)
        undoButton.setEnabled(False)
        self.undoStack.cleanChanged.connect(lambda: undoButton.setEnabled(self.undoStack.count() > 0))
        self.undoMenu = QtWidgets.QMenu()
        self.undoMenu.addAction(self.undoStack.createUndoAction(self))
        self.undoMenu.addAction(self.undoStack.createRedoAction(self))
        action = self.undoMenu.addAction('Show History...')
        action.triggered.connect(lambda: self.showUndoStack())
        undoButton.setMenu(self.undoMenu)

        QtWidgets.QShortcut("Ctrl+S", self, self.saveCurrentProject)
        QtWidgets.QShortcut("Ctrl+A", self, self.openSaveProjectDialog)
        QtWidgets.QShortcut("Ctrl+O", self, self.openOpenProjectDialog)
        QtWidgets.QShortcut("Ctrl+N", self, self.createNewProject)
        QtWidgets.QShortcut("Ctrl+Z", self, self.undoStack.undo)
        QtWidgets.QShortcut("Ctrl+Y", self, self.undoStack.redo)
        QtWidgets.QShortcut("Ctrl+Shift+Z", self, self.undoStack.redo)
        QtWidgets.QShortcut("Ctrl+Shift+U", self, self.showUndoStack)


    def _setup_component_list_ui(self) -> None:
        """Configures component list UI elements and hotkeys."""
        style = self.pushButton_listMoveUp.style()
        self.pushButton_listMoveUp.setIcon(style.standardIcon(QtWidgets.QStyle.SP_ArrowUp))
        style = self.pushButton_listMoveDown.style()
        self.pushButton_listMoveDown.setIcon(style.standardIcon(QtWidgets.QStyle.SP_ArrowDown))
        style = self.pushButton_removeComponent.style()
        self.pushButton_removeComponent.setIcon(style.standardIcon(QtWidgets.QStyle.SP_DialogDiscardButton))

        componentList = self.listWidget_componentList
        componentList.dropEvent = self.dragComponent  # type: ignore
        componentList.itemSelectionChanged.connect(self.changeComponentWidget)
        componentList.itemSelectionChanged.connect(self.presetManager.clearPresetListSelection)
        self.pushButton_removeComponent.clicked.connect(lambda: self.removeComponent())

        componentList.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        componentList.customContextMenuRequested.connect(self.componentContextMenu)

        self.pushButton_listMoveUp.clicked.connect(lambda: self.moveComponent(-1))
        self.pushButton_listMoveDown.clicked.connect(lambda: self.moveComponent(1))

        for inskey in ("Ctrl+T", QtCore.Qt.Key_Insert):
            QtWidgets.QShortcut(inskey, self, activated=lambda: self.pushButton_addComponent.click())
        for delkey in ("Ctrl+R", QtCore.Qt.Key_Delete):
            QtWidgets.QShortcut(delkey, self.listWidget_componentList, self.removeComponent)
        QtWidgets.QShortcut("Ctrl+Space", self, activated=lambda: self.listWidget_componentList.setFocus())
        QtWidgets.QShortcut("Ctrl+Shift+S", self, self.presetManager.openSavePresetDialog)
        QtWidgets.QShortcut("Ctrl+Shift+C", self, self.presetManager.clearPreset)
        QtWidgets.QShortcut("Ctrl+Up", self.listWidget_componentList, activated=lambda: self.moveComponent(-1))
        QtWidgets.QShortcut("Ctrl+Down", self.listWidget_componentList, activated=lambda: self.moveComponent(1))
        QtWidgets.QShortcut("Ctrl+Home", self.listWidget_componentList, activated=lambda: self.moveComponent('top'))
        QtWidgets.QShortcut("Ctrl+End", self.listWidget_componentList, activated=lambda: self.moveComponent('bottom'))

        if log.isEnabledFor(logging.DEBUG):
            QtWidgets.QShortcut("Ctrl+Alt+Shift+R", self, self.drawPreview)
            QtWidgets.QShortcut("Ctrl+Alt+Shift+A", self, lambda: log.debug(repr(self)))

    def _setup_export_ui(self) -> None:
        """Configures Export UI elements."""
        if sys.platform == 'darwin':
            log.debug('Darwin detected: showing progress label below progress bar')
            self.progressBar_createVideo.setTextVisible(False)
        else:
            self.progressLabel.setHidden(True)

        self.toolButton_selectAudioFile.clicked.connect(self.openInputFileDialog)
        self.toolButton_selectOutputFile.clicked.connect(self.openOutputFileDialog)
        self.lineEdit_audioFile.textChanged.connect(self.autosave_and_update_title)
        self.lineEdit_outputFile.textChanged.connect(self.autosave_and_update_title)
        self.progressBar_createVideo.setValue(0)
        self.pushButton_createVideo.clicked.connect(self.createAudioVisualization)
        self.pushButton_Cancel.clicked.connect(self.stopVideo)
        QtWidgets.QShortcut("Ctrl+Shift+F", self, self.showFfmpegCommand)


    def _setup_encoder_settings_ui(self) -> None:
        """Configures Encoder Settings UI elements."""
        for i, container in enumerate(Core.encoderOptions['containers']):
            self.comboBox_videoContainer.addItem(container['name'])
            if container['name'] == self.settings.value('outputContainer'):
                self.comboBox_videoContainer.setCurrentIndex(i)

        self.comboBox_videoContainer.currentIndexChanged.connect(self.updateCodecs)
        self.updateCodecs()

        for codec_type, comboBox in [('Video', self.comboBox_videoCodec), ('Audio', self.comboBox_audioCodec)]:
            for i in range(comboBox.count()):
                codec = comboBox.itemText(i)
                if codec == self.settings.value(f'output{codec_type}Codec'):
                    comboBox.setCurrentIndex(i)

        self.comboBox_videoCodec.currentIndexChanged.connect(self.updateCodecSettings)
        self.comboBox_audioCodec.currentIndexChanged.connect(self.updateCodecSettings)

        vBitrate = int(self.settings.value('outputVideoBitrate', "2500"))
        aBitrate = int(self.settings.value('outputAudioBitrate', "192"))

        for spinBox, bitrate in [(self.spinBox_vBitrate, vBitrate), (self.spinBox_aBitrate, aBitrate)]:
            spinBox.setValue(bitrate)
            spinBox.valueChanged.connect(self.updateCodecSettings)


    def _setup_projects_menu(self) -> None:
        """Configures the Projects Menu and its actions."""
        self.projectMenu = QtWidgets.QMenu()
        self.menuButton_newProject = self.projectMenu.addAction("New Project")
        self.menuButton_newProject.triggered.connect(lambda: self.createNewProject())
        self.menuButton_openProject = self.projectMenu.addAction("Open Project")
        self.menuButton_openProject.triggered.connect(lambda: self.openOpenProjectDialog())

        action = self.projectMenu.addAction("Save Project")
        action.triggered.connect(self.saveCurrentProject)

        action = self.projectMenu.addAction("Save Project As")
        action.triggered.connect(self.openSaveProjectDialog)

        self.pushButton_projects.setMenu(self.projectMenu)


    def _setup_presets_button(self) -> None:
        """Configures the Presets Button."""
        self.pushButton_presets.clicked.connect(self.openPresetManager)


    def _setup_undo_dialog(self) -> QtWidgets.QDialog:
        """Creates and configures the Undo History dialog."""
        undoDialog = QtWidgets.QDialog(self)
        undoDialog.setWindowTitle("Undo History")
        undoView = QtWidgets.QUndoView(self.undoStack)
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(undoView)
        undoDialog.setLayout(layout)
        return undoDialog


    def _setup_preview_window(self) -> PreviewWindow:
        """Creates and configures the preview window."""
        previewWindow = PreviewWindow(self, os.path.join(Core.wd, 'gui', "background.png"))
        self.verticalLayout_previewWrapper.addWidget(previewWindow)
        return previewWindow


    def _setup_preview_thread(self) -> Tuple[Queue[List[Any]], QtCore.QThread, preview_thread.Worker]:
        """Creates and configures the preview thread, queue, and worker."""
        previewQueue: Queue[List[Any]] = Queue()
        previewThread = QtCore.QThread(self)
        previewWorker = preview_thread.Worker(self.core, self.settings, previewQueue)
        previewWorker.moveToThread(previewThread)
        self.newTask.connect(previewWorker.createPreviewImage)
        self.processTask.connect(previewWorker.process)
        previewWorker.error.connect(self.previewWindow.threadError)
        previewWorker.imageCreated.connect(self.showPreviewImage)
        previewThread.start()
        previewThread.finished.connect(lambda: log.info('Preview thread finished.'))
        return previewQueue, previewThread, previewWorker


    def _setup_preview_timer(self) -> QtCore.QTimer:
        """Creates and configures the preview timer."""
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.processTask.emit)
        timer.start(500)  # 500 ms timeout
        return timer


    def resize_window_for_dpi(self, dpi: float) -> None:
        """Resizes the main window based on screen DPI."""
        self.resize(
            int(self.width() * (dpi / 144)),
            int(self.height() * (dpi / 144))
        )


    def autosave_and_update_title(self) -> None:
        """Performs autosave and updates window title - for UI element connections"""
        self.autosave()
        self.updateWindowTitle()


    def _load_project_on_startup(self, project_path: str) -> None:
        """Loads a project from file on application startup."""
        if not project_path.endswith('.avp'):
            project_path += '.avp'

        if not os.path.dirname(project_path):
            project_path = os.path.join(self.settings.value("projectDir"), project_path)

        self.currentProject = project_path
        self.settings.setValue("currentProject", project_path)
        if os.path.exists(self.autosavePath):
            os.remove(self.autosavePath)


    def _restore_last_project(self) -> None:
        """Restores the last opened project or autosave on startup."""
        self.currentProject = self.settings.value("currentProject")

        if self.autosaveExists(identical=True):
            os.remove(self.autosavePath)

        if self.currentProject and os.path.exists(self.autosavePath):
            if self.showMessage(
                msg=f"Restore unsaved changes in project '{os.path.basename(self.currentProject)[:-4]}?'",
                showCancel=True,
                ) :
                self.saveProjectChanges()
            else:
                os.remove(self.autosavePath)


    def _verify_ffmpeg(self) -> None:
        """Verifies that FFmpeg is found and displays a warning if not."""
        if not self.core.FFMPEG_BIN:
            self.showMessage(
                msg="FFmpeg could not be found.  "
                    "Install FFmpeg, or place it in the same folder as this program.",
                icon='Critical'
            )
        elif not self.settings.value("ffmpegMsgShown"):
            try:
                with open(os.devnull, "w") as f:
                    ffmpegVers = checkOutput([self.core.FFMPEG_BIN, '-version'], stderr=f)
                goodVersion = str(ffmpegVers).split()[2].startswith('4') # type: ignore
            except Exception:
                goodVersion = False

            if not goodVersion:
                self.showMessage(
                    msg="You're using an old version of FFmpeg. "
                        "Some features may not work as expected."
                )
            self.settings.setValue("ffmpegMsgShown", True)


    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        log.info('Ending the preview thread')
        self.timer.stop()
        if hasattr(self, "previewThread"):
            self.previewThread.quit()
            self.previewThread.wait()
        super().closeEvent(event)

    @disableWhenOpeningProject
    def updateWindowTitle(self) -> None:
        log.debug("Setting main window's title")
        windowTitle = appName
        try:
            if self.currentProject:
                windowTitle += ' - %s' % \
                    os.path.splitext(
                        os.path.basename(self.currentProject))[0]
            if self.autosaveExists(identical=False):
                windowTitle += '*'
        except AttributeError:
            pass
        log.verbose(f'Window title is "{windowTitle}"')
        self.setWindowTitle(windowTitle)

    @QtCore.pyqtSlot(int, dict)
    def updateComponentTitle(self, pos: int, presetStore: Union[bool, Dict[str, Any]] = False) -> None:
        '''
            Sets component title to modified or unmodified when given boolean.
            If given a preset dict, compares it against the component to
            determine if it is modified.
            A component with no preset is always unmodified.
        '''
        if type(presetStore) is dict:
            name = presetStore['preset']
            if name is None or name not in self.core.savedPresets:
                modified = False
            else:
                modified = (presetStore != self.core.savedPresets[name])

        else:
            modified = bool(presetStore)

        if pos < 0:
            pos = len(self.core.selectedComponents)-1
        name = self.core.selectedComponents[pos].name
        title = str(name)
        if self.core.selectedComponents[pos].currentPreset:
            title += ' - %s' % self.core.selectedComponents[pos].currentPreset
            if modified:
                title += '*'
        if type(presetStore) is bool:
            log.debug(
                'Forcing %s #%s\'s modified status to %s: %s',
                name, pos, modified, title
            )
        else:
            log.debug(
                'Setting %s #%s\'s title: %s',
                name, pos, title
            )
        self.listWidget_componentList.item(pos).setText(title)


    def update_component_display(self, selected_index: int) -> None:
        """Updates the component list and stacked widget after a move."""
        self.listWidget_componentList.clear()
        for comp in self.core.selectedComponents:
            self.listWidget_componentList.addItem(comp.name)  # Reset titles

        self.listWidget_componentList.setCurrentRow(selected_index)
        self.stackedWidget.setCurrentIndex(selected_index)

    def updateCodecs(self) -> None:
        containerWidget = self.comboBox_videoContainer
        vCodecWidget = self.comboBox_videoCodec
        aCodecWidget = self.comboBox_audioCodec
        index = containerWidget.currentIndex()
        name = containerWidget.itemText(index)
        self.settings.setValue('outputContainer', name)

        vCodecWidget.clear()
        aCodecWidget.clear()

        for container in Core.encoderOptions['containers']:
            if container['name'] == name:
                for vCodec in container['video-codecs']:
                    vCodecWidget.addItem(vCodec)
                for aCodec in container['audio-codecs']:
                    aCodecWidget.addItem(aCodec)

    def updateCodecSettings(self) -> None:
        '''Updates settings.ini to match encoder option widgets'''
        vCodecWidget = self.comboBox_videoCodec
        vBitrateWidget = self.spinBox_vBitrate
        aBitrateWidget = self.spinBox_aBitrate
        aCodecWidget = self.comboBox_audioCodec
        currentVideoCodec = vCodecWidget.currentIndex()
        currentVideoCodec = vCodecWidget.itemText(currentVideoCodec)
        currentVideoBitrate = vBitrateWidget.value()
        currentAudioCodec = aCodecWidget.currentIndex()
        currentAudioCodec = aCodecWidget.itemText(currentAudioCodec)
        currentAudioBitrate = aBitrateWidget.value()
        self.settings.setValue('outputVideoCodec', currentVideoCodec)
        self.settings.setValue('outputAudioCodec', currentAudioCodec)
        self.settings.setValue('outputVideoBitrate', currentVideoBitrate)
        self.settings.setValue('outputAudioBitrate', currentAudioBitrate)

    @disableWhenOpeningProject
    def autosave(self, force: bool = False) -> None:
        if not self.currentProject:
            if os.path.exists(self.autosavePath):
                os.remove(self.autosavePath)
        elif force or time.time() - self.lastAutosave >= self.autosaveCooldown:
            self.core.createProjectFile(self.autosavePath, self)
            self.lastAutosave = time.time()
            if len(self.autosaveTimes) >= 5:
                # Do some math to reduce autosave spam. This gives a smooth
                # curve up to 5 seconds cooldown and maintains that for 30 secs
                # if a component is continuously updated
                timeDiff = self.lastAutosave - self.autosaveTimes.pop()
                if not force and timeDiff >= 1.0 \
                        and timeDiff <= 10.0:
                    if self.autosaveCooldown / 4.0 < 0.5:
                        self.autosaveCooldown += 1.0
                    self.autosaveCooldown = (
                            5.0 * (self.autosaveCooldown / 5.0)
                        ) + (self.autosaveCooldown / 5.0) * 2
                elif force or timeDiff >= self.autosaveCooldown * 5:
                    self.autosaveCooldown = 0.2
            self.autosaveTimes.insert(0, self.lastAutosave)
        else:
            log.debug('Autosave rejected by cooldown')

    def autosaveExists(self, identical: bool = True) -> bool:
        '''Determines if creating the autosave should be blocked.'''
        try:
            if self.currentProject and os.path.exists(self.autosavePath) \
                and filecmp.cmp(
                    self.autosavePath, self.currentProject) == identical:
                log.debug(
                    'Autosave found %s to be identical'
                    % 'not' if not identical else ''
                )
                return True
        except FileNotFoundError:
            log.error(
                'Project file couldn\'t be located: %s', self.currentProject)
            return identical
        return False

    def saveProjectChanges(self) -> bool:
        '''Overwrites project file with autosave file'''
        try:
            os.remove(self.currentProject)  # type: ignore
            os.rename(self.autosavePath, self.currentProject)  # type: ignore
            return True
        except (FileNotFoundError, IsADirectoryError, TypeError) as e:
            self.showMessage(
                msg='Project file couldn\'t be saved.',
                detail=str(e))
            return False

    def openInputFileDialog(self) -> None:
        inputDir = self.settings.value("inputDir", os.path.expanduser("~"))

        fileName, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Audio File",
            self.inputDir, "Audio Files (%s)" % " ".join(Core.audioFormats))

        if fileName:
            self.settings.setValue("inputDir", os.path.dirname(fileName))
            self.lineEdit_audioFile.setText(fileName)

    def openOutputFileDialog(self) -> None:
        outputDir = self.settings.value("outputDir", os.path.expanduser("~"))

        fileName, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Set Output Video File",
            outputDir,
            "Video Files (%s);; All Files (*)" % " ".join(
                Core.videoFormats))

        if fileName:
            self.settings.setValue("outputDir", os.path.dirname(fileName))
            self.lineEdit_outputFile.setText(fileName)

    def stopVideo(self) -> None:
        log.info('Export cancelled')
        if hasattr(self, "videoWorker"):
            self.videoWorker.cancel()
        self.canceled = True

    @QtCore.pyqtSlot(str, str)
    def videoThreadError(self, msg: str, detail: str) -> None:
        try:
            self.stopVideo()
        except AttributeError as e:
            if 'videoWorker' not in str(e):
                raise
        self.showMessage(
            msg=msg,
            detail=detail,
            icon='Critical',
        )
        log.info('%s', repr(self))


    def changeEncodingStatus(self, status: bool) -> None:
        self.encoding = status
        if status:
            self._disable_encoding_ui()
        else:
            self._enable_encoding_ui()


    def _disable_encoding_ui(self) -> None:
        """Disables UI elements when encoding starts."""
        self.pushButton_createVideo.setEnabled(False)
        self.pushButton_Cancel.setEnabled(True)
        self.comboBox_resolution.setEnabled(False)
        self.stackedWidget.setEnabled(False)
        self.tab_encoderSettings.setEnabled(False)
        self.label_audioFile.setEnabled(False)
        self.toolButton_selectAudioFile.setEnabled(False)
        self.label_outputFile.setEnabled(False)
        self.toolButton_selectOutputFile.setEnabled(False)
        self.lineEdit_audioFile.setEnabled(False)
        self.lineEdit_outputFile.setEnabled(False)
        self.listWidget_componentList.setEnabled(False)
        self.pushButton_addComponent.setEnabled(False)
        self.pushButton_removeComponent.setEnabled(False)
        self.pushButton_listMoveDown.setEnabled(False)
        self.pushButton_listMoveUp.setEnabled(False)
        self.pushButton_undo.setEnabled(False)
        self.menuButton_newProject.setEnabled(False)
        self.menuButton_openProject.setEnabled(False)
        self.undoDialog.close()
        if sys.platform == 'darwin':
            self.progressLabel.setHidden(False)


    def _enable_encoding_ui(self) -> None:
        """Enables UI elements when encoding finishes/cancels."""
        self.pushButton_createVideo.setEnabled(True)
        self.pushButton_Cancel.setEnabled(False)
        self.comboBox_resolution.setEnabled(True)
        self.stackedWidget.setEnabled(True)
        self.tab_encoderSettings.setEnabled(True)
        self.label_audioFile.setEnabled(True)
        self.toolButton_selectAudioFile.setEnabled(True)
        self.lineEdit_audioFile.setEnabled(True)
        self.label_outputFile.setEnabled(True)
        self.toolButton_selectOutputFile.setEnabled(True)
        self.lineEdit_outputFile.setEnabled(True)
        self.pushButton_addComponent.setEnabled(True)
        self.pushButton_removeComponent.setEnabled(True)
        self.pushButton_listMoveDown.setEnabled(True)
        self.pushButton_listMoveUp.setEnabled(True)
        self.pushButton_undo.setEnabled(True)
        self.menuButton_newProject.setEnabled(True)
        self.menuButton_openProject.setEnabled(True)
        self.listWidget_componentList.setEnabled(True)
        self.progressLabel.setHidden(True)
        self.drawPreview(True)


    @QtCore.pyqtSlot(int)
    def progressBarUpdated(self, value: int) -> None:
        self.progressBar_createVideo.setValue(value)

    @QtCore.pyqtSlot(str)
    def progressBarSetText(self, value: str) -> None:
        if sys.platform == 'darwin':
            self.progressLabel.setText(value)
        else:
            self.progressBar_createVideo.setFormat(value)

    def updateResolution(self) -> None:
        resIndex: int = int(self.comboBox_resolution.currentIndex())
        res: List[str] = Core.resolutions[resIndex].split('x')
        changed: bool = res[0] != self.settings.value("outputWidth")
        self.settings.setValue('outputWidth', res[0])
        self.settings.setValue('outputHeight', res[1])
        if changed:
            for i in range(len(self.core.selectedComponents):
                self.core.updateComponent(i)

    def drawPreview(self, force: bool = False, **kwargs: Any) -> None:
        '''Use autosave keyword arg to force saving or not saving if needed'''
        self.newTask.emit(self.core.selectedComponents)
        # self.processTask.emit() # Removed as per the plan
        if force or 'autosave' in kwargs:
            if force or kwargs['autosave']:
                self.autosave(True)
        else:
            self.autosave()
        self.updateWindowTitle()

    @QtCore.pyqtSlot('QImage')
    def showPreviewImage(self, image: QtGui.QImage) -> None:
        self.previewWindow.changePixmap(image)

    @disableWhenEncoding
    def showUndoStack(self) -> None:
        self.undoDialog.show()

    def showFfmpegCommand(self) -> None:
        from textwrap import wrap
        from ..toolkit.ffmpeg import createFfmpegCommand
        command = createFfmpegCommand(
            self.lineEdit_audioFile.text(),
            self.lineEdit_outputFile.text(),
            self.core.selectedComponents
        )
        if command:
            command_str = " ".join(command)
            log.info(f"FFmpeg command: {command_str}")
            lines = wrap(command_str, 49)  # Wrap for display
            self.showMessage(
                msg=f"Current FFmpeg command:\n\n{' '.join(lines)}"
            )
        else:
            self.showMessage(msg="Could not generate FFmpeg command. See log for details.")

    def addComponent(self, compPos: int, moduleIndex: int) -> None:
        '''Creates an undoable action that adds a new component.'''
        action = AddComponent(self, compPos, moduleIndex)
        self.undoStack.push(action)

    def insertComponent(self, index: int) -> int:
        '''Triggered by Core to finish initializing a new component.'''
        componentList = self.listWidget_componentList
        stackedWidget = self.stackedWidget

        componentList.insertItem(
            index,
            self.core.selectedComponents[index].name)
        componentList.setCurrentRow(index)

        # connect to signal that adds an asterisk when modified
        self.core.selectedComponents[index].modified.connect(
            self.updateComponentTitle)

        self.pages.insert(index, self.core.selectedComponents[index].page)
        stackedWidget.insertWidget(index, self.pages[index])
        stackedWidget.setCurrentIndex(index)

        return index

    def removeComponent(self) -> None:
        componentList = self.listWidget_componentList
        selected = componentList.selectedItems()
        if selected:
            action = RemoveComponent(self, selected)
            self.undoStack.push(action)

    def _removeComponent(self, index: int) -> None:
        #The logic from removeComponent was extracted to this method, for easier use
        stackedWidget = self.stackedWidget
        componentList = self.listWidget_componentList
        stackedWidget.removeWidget(self.pages[index])
        componentList.takeItem(index)
        self.core.removeComponent(index)
        self.pages.pop(index)
        self.changeComponentWidget()
        self.drawPreview()

    @disableWhenEncoding
    def moveComponent(self, change: Union[int, str]) -> None:
        '''Moves a component relatively from its current position'''
        componentList = self.listWidget_componentList
        currentRow = componentList.currentRow()

        if currentRow == -1:  # No selection
            return

        if change == 'top':
            newRow = 0
        elif change == 'bottom':
            newRow = componentList.count() - 1
        else:
            newRow = currentRow + change

        if 0 <= newRow < componentList.count():
            action = MoveComponent(self, currentRow, newRow)
            self.undoStack.push(action)

    def getComponentListMousePos(self, position: QtCore.QPoint) -> int:
        '''
        Given a QPos, returns the component index under the mouse cursor
        or -1 if no component is there.
        '''
        componentList = self.listWidget_componentList

        # Iterate through items and check if the position is within their visual rect
        for i in range(componentList.count()):
            item = componentList.item(i)
            rect = componentList.visualItemRect(item)
            if rect.contains(position):
                return i  # Return the index of the item under the mouse

        return -1  # No item found at the given position


    @disableWhenEncoding
    def dragComponent(self, event: QtGui.QDropEvent) -> None:
        '''Used as Qt drop event for the component listwidget'''
        componentList = self.listWidget_componentList
        mousePos = self.getComponentListMousePos(event.pos())
        if mousePos > -1:
            change = (componentList.currentRow() - mousePos) * -1
        else:
            change = (componentList.count() - componentList.currentRow() - 1)
        self.moveComponent(change)

    def changeComponentWidget(self) -> None:
        selected = self.listWidget_componentList.selectedItems()
        if selected:
            index = self.listWidget_componentList.row(selected[0])
            self.stackedWidget.setCurrentIndex(index)

    def openPresetManager(self) -> None:
        '''Preset manager for importing, exporting, renaming, deleting'''
        self.presetManager.show_()

    def clear(self) -> None:
        '''Get a blank slate'''
        self.core.clearComponents()
        self.listWidget_componentList.clear()
        for widget in self.pages:
            self.stackedWidget.removeWidget(widget)
        self.pages = []
        for field in (
                self.lineEdit_audioFile,
                self.lineEdit_outputFile
                ):
            with blockSignals(field):
                field.setText('')
        self.progressBarUpdated(0)
        self.progressBarSetText('')
        self.undoStack.clear()

    @disableWhenEncoding
    def createNewProject(self, prompt: bool = True) -> None:
        if prompt:
            self.openSaveChangesDialog('starting a new project')

        self.clear()
        self.currentProject = None
        self.settings.setValue("currentProject", None)
        self.drawPreview(True)

    def saveCurrentProject(self) -> None:
        if self.currentProject:
            self.core.createProjectFile(self.currentProject, self)
            try:
                os.remove(self.autosavePath)
            except FileNotFoundError:
                pass
            self.updateWindowTitle()
        else:
            self.openSaveProjectDialog()

    def openSaveChangesDialog(self, phrase: str) -> None:
        success = True
        if self.autosaveExists(identical=False):
            ch = self.showMessage(
                msg="You have unsaved changes in project '%s'. "
                "Save before %s?" % (
                    os.path.basename(self.currentProject)[:-4],  # type: ignore
                    phrase
                ),
                showCancel=True)
            if ch:
                success = self.saveProjectChanges()

        if success and os.path.exists(self.autosavePath):
            os.remove(self.autosavePath)

    def openSaveProjectDialog(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Create Project File",
            self.settings.value("projectDir"),
            "Project Files (*.avp)")
        if not filename:
            return
        if not filename.endswith(".avp"):
            filename += '.avp'
        self.settings.setValue("projectDir", os.path.dirname(filename))
        self.settings.setValue("currentProject", filename)
        self.currentProject = filename
        self.core.createProjectFile(filename, self)
        self.updateWindowTitle()

    @disableWhenEncoding
    def openOpenProjectDialog(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Project File",
            self.settings.value("projectDir"),
            "Project Files (*.avp)")
        self.openProject(filename)

    def openProject(self, filepath: Optional[str], prompt: bool = True) -> None:
        if not filepath or not os.path.exists(filepath) \
                or not filepath.endswith('.avp'):
            return

        self.clear()
        # ask to save any changes that are about to get deleted
        if prompt:
            self.openSaveChangesDialog('opening another project')

        self.currentProject = filepath
        self.settings.setValue("currentProject", filepath)
        self.settings.setValue("projectDir", os.path.dirname(filepath))
        # actually load the project using core method
        self.core.openProject(self, filepath)
        self.drawPreview(autosave=False)
        self.updateWindowTitle()

    def showMessage(self,  msg: str,  detail: str = "", icon: str = 'Information', showCancel: bool = False, parent: Optional[QtWidgets.QWidget] = None) -> bool:

        if parent is None:
            parent = self
        msg_box = QtWidgets.QMessageBox(parent)
        msg_box.setWindowTitle(appName)
        msg_box.setModal(True)
        msg_box.setText(msg)
        msg_box.setIcon(
            eval('QtWidgets.QMessageBox.%s' % icon)
            if icon else QtWidgets.QMessageBox.Information
        )
        msg_box.setDetailedText(detail if detail else None)
        if showCancel:
            msg_box.setStandardButtons(
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        else:
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        ch = msg_box.exec_()
        if ch == 1024:
            return True
        return False

    @disableWhenEncoding
    def componentContextMenu(self, QPos: QtCore.QPoint) -> None:
        '''Appears when right-clicking the component list'''
        componentList = self.listWidget_componentList
        self.menu: QtWidgets.QMenu = QtWidgets.QMenu()  # Type hint
        parentPosition = componentList.mapToGlobal(QtCore.QPoint(0, 0))

        index = self.getComponentListMousePos(QPos)
        if index > -1:
            # Show preset menu if clicking a component
            self.presetManager.findPresets()
            menuItem = self.menu.addAction("Save Preset")
            menuItem.triggered.connect(
                self.presetManager.openSavePresetDialog
            )

            # submenu for opening presets
            try:
                presets = self.presetManager.presets[
                    str(self.core.selectedComponents[index])
                ]
                self.presetSubmenu: QtWidgets.QMenu = QtWidgets.QMenu("Open Preset")  # Type hint
                self.menu.addMenu(self.presetSubmenu)

                for version, presetName in presets:
                    menuItem = self.presetSubmenu.addAction(presetName)
                    menuItem.triggered.connect(
                        lambda _, presetName=presetName:
                            self.presetManager.openPreset(presetName)
                    )
            except KeyError:
                pass

            if self.core.selectedComponents[index].currentPreset:
                menuItem = self.menu.addAction("Clear Preset")
                menuItem.triggered.connect(
                    self.presetManager.clearPreset
                )
            self.menu.addSeparator()

        # "Add Component" submenu
        self.submenu: QtWidgets.QMenu = QtWidgets.QMenu("Add")  # Type hint
        self.menu.addMenu(self.submenu)
        insertCompAtTop = self.settings.value("pref_insertCompAtTop")
        for i, comp in enumerate(self.core.modules):
            menuItem = self.submenu.addAction(comp.Component.name)
            menuItem.triggered.connect(
                lambda _, item=i: self.addComponent(
                    0 if insertCompAtTop else index, item
                )
            )

        self.pushButton_projects.setMenu(self.projectMenu)
        # Hotkeys for projects
        QtWidgets.QShortcut("Ctrl+S", self, self.saveCurrentProject)
        QtWidgets.QShortcut("Ctrl+A", self, self.openSaveProjectDialog)
        QtWidgets.QShortcut("Ctrl+O", self, self.openOpenProjectDialog)
        QtWidgets.QShortcut("Ctrl+N", self, self.createNewProject)

        # Hotkeys for undo/redo
        QtWidgets.QShortcut("Ctrl+Z", self, self.undoStack.undo)
        QtWidgets.QShortcut("Ctrl+Y", self, self.undoStack.redo)
        QtWidgets.QShortcut("Ctrl+Shift+Z", self, self.undoStack.redo)

        # Hotkeys for component list
        for inskey in ("Ctrl+T", QtCore.Qt.Key_Insert):
            QtWidgets.QShortcut(
                inskey, self,
                activated=lambda: self.pushButton_addComponent.click()
            )
        for delkey in ("Ctrl+R", QtCore.Qt.Key_Delete):
            QtWidgets.QShortcut(
                delkey, self.listWidget_componentList,
                self.removeComponent
            )
        QtWidgets.QShortcut(
            "Ctrl+Space", self,
            activated=lambda: self.listWidget_componentList.setFocus()
        )
        QtWidgets.QShortcut(
            "Ctrl+Shift+S", self,
            self.presetManager.openSavePresetDialog
        )
        QtWidgets.QShortcut(
            "Ctrl+Shift+C", self, self.presetManager.clearPreset
        )

        QtWidgets.QShortcut(
            "Ctrl+Up", self.listWidget_componentList,
            activated=lambda: self.moveComponent(-1)
        )
        QtWidgets.QShortcut(
            "Ctrl+Down", self.listWidget_componentList,
            activated=lambda: self.moveComponent(1)
        )
        QtWidgets.QShortcut(
            "Ctrl+Home", self.listWidget_componentList,
            activated=lambda: self.moveComponent('top')
        )
        QtWidgets.QShortcut(
            "Ctrl+End", self.listWidget_componentList,
            activated=lambda: self.moveComponent('bottom')
        )

        QtWidgets.QShortcut(
            "Ctrl+Shift+F", self, self.showFfmpegCommand
        )
        QtWidgets.QShortcut(
            "Ctrl+Shift+U", self, self.showUndoStack
        )

        if log.isEnabledFor(logging.DEBUG):
            QtWidgets.QShortcut(
                "Ctrl+Alt+Shift+R", self, self.drawPreview
            )
            QtWidgets.QShortcut(
                "Ctrl+Alt+Shift+A", self, lambda: log.debug(repr(self))
            )

        # Close MainWindow when receiving Ctrl+C from terminal
        signal.signal(signal.SIGINT, lambda *args: self.close())

        # Add initial components if none are in the list
        if not self.core.selectedComponents:
            self.core.insertComponent(0, 0, self)
            self.core.insertComponent(1, 1, self)

    def __repr__(self) -> str:
        return (
            '%s\n'
            '\n%s\n'
            '#####\n'
            'Preview thread is %s\n' % (
                super().__repr__(),
                "core not initialized" if not hasattr(self, "core") else repr(self.core),
                'live' if hasattr(self, "previewThread") and self.previewThread.isRunning() else 'dead',
            )
        )

    @disableWhenOpeningProject
    def updateWindowTitle(self) -> None:
        log.debug("Setting main window's title")
        windowTitle = appName
        try:
            if self.currentProject:
                windowTitle += ' - %s' % \
                    os.path.splitext(
                        os.path.basename(self.currentProject))[0]
            if self.autosaveExists(identical=False):
                windowTitle += '*'
        except AttributeError:
            pass
        log.verbose(f'Window title is "{windowTitle}"')
        self.setWindowTitle(windowTitle)

    @QtCore.pyqtSlot(int, dict)
    def updateComponentTitle(self, pos: int, presetStore: Union[bool, Dict[str, Any]] = False) -> None:
        '''
            Sets component title to modified or unmodified when given boolean.
            If given a preset dict, compares it against the component to
            determine if it is modified.
            A component with no preset is always unmodified.
        '''
        if type(presetStore) is dict:
            name = presetStore['preset']
            if name is None or name not in self.core.savedPresets:
                modified = False
            else:
                modified = (presetStore != self.core.savedPresets[name])

        else:
            modified = bool(presetStore)

        if pos < 0:
            pos = len(self.core.selectedComponents)-1
        name = self.core.selectedComponents[pos].name
        title = str(name)
        if self.core.selectedComponents[pos].currentPreset:
            title += ' - %s' % self.core.selectedComponents[pos].currentPreset
            if modified:
                title += '*'
        if type(presetStore) is bool:
            log.debug(
                'Forcing %s #%s\'s modified status to %s: %s',
                name, pos, modified, title
            )
        else:
            log.debug(
                'Setting %s #%s\'s title: %s',
                name, pos, title
            )
        self.listWidget_componentList.item(pos).setText(title)


    def update_component_display(self, selected_index: int) -> None:
        """Updates the component list and stacked widget after a move."""
        self.listWidget_componentList.clear()
        for comp in self.core.selectedComponents:
            self.listWidget_componentList.addItem(comp.name)  # Reset titles

        self.listWidget_componentList.setCurrentRow(selected_index)
        self.stackedWidget.setCurrentIndex(selected_index)

    def updateCodecs(self) -> None:
        containerWidget = self.comboBox_videoContainer
        vCodecWidget = self.comboBox_videoCodec
        aCodecWidget = self.comboBox_audioCodec
        index = containerWidget.currentIndex()
        name = containerWidget.itemText(index)
        self.settings.setValue('outputContainer', name)

        vCodecWidget.clear()
        aCodecWidget.clear()

        for container in Core.encoderOptions['containers']:
            if container['name'] == name:
                for vCodec in container['video-codecs']:
                    vCodecWidget.addItem(vCodec)
                for aCodec in container['audio-codecs']:
                    aCodecWidget.addItem(aCodec)

    def updateCodecSettings(self) -> None:
        '''Updates settings.ini to match encoder option widgets'''
        vCodecWidget = self.comboBox_videoCodec
        vBitrateWidget = self.spinBox_vBitrate
        aBitrateWidget = self.spinBox_aBitrate
        aCodecWidget = self.comboBox_audioCodec
        currentVideoCodec = vCodecWidget.currentIndex()
        currentVideoCodec = vCodecWidget.itemText(currentVideoCodec)
        currentVideoBitrate = vBitrateWidget.value()
        currentAudioCodec = aCodecWidget.currentIndex()
        currentAudioCodec = aCodecWidget.itemText(currentAudioCodec)
        currentAudioBitrate = aBitrateWidget.value()
        self.settings.setValue('outputVideoCodec', currentVideoCodec)
        self.settings.setValue('outputAudioCodec', currentAudioCodec)
        self.settings.setValue('outputVideoBitrate', currentVideoBitrate)
        self.settings.setValue('outputAudioBitrate', currentAudioBitrate)

    @disableWhenOpeningProject
    def autosave(self, force: bool = False) -> None:
        if not self.currentProject:
            if os.path.exists(self.autosavePath):
                os.remove(self.autosavePath)
        elif force or time.time() - self.lastAutosave >= self.autosaveCooldown:
            self.core.createProjectFile(self.autosavePath, self)
            self.lastAutosave = time.time()
            if len(self.autosaveTimes) >= 5:
                # Do some math to reduce autosave spam. This gives a smooth
                # curve up to 5 seconds cooldown and maintains that for 30 secs
                # if a component is continuously updated
                timeDiff = self.lastAutosave - self.autosaveTimes.pop()
                if not force and timeDiff >= 1.0 \
                        and timeDiff <= 10.0:
                    if self.autosaveCooldown / 4.0 < 0.5:
                        self.autosaveCooldown += 1.0
                    self.autosaveCooldown = (
                            5.0 * (self.autosaveCooldown / 5.0)
                        ) + (self.autosaveCooldown / 5.0) * 2
                elif force or timeDiff >= self.autosaveCooldown * 5:
                    self.autosaveCooldown = 0.2
            self.autosaveTimes.insert(0, self.lastAutosave)
        else:
            log.debug('Autosave rejected by cooldown')

    def autosaveExists(self, identical: bool = True) -> bool:
        '''Determines if creating the autosave should be blocked.'''
        try:
            if self.currentProject and os.path.exists(self.autosavePath) \
                and filecmp.cmp(
                    self.autosavePath, self.currentProject) == identical:
                log.debug(
                    'Autosave found %s to be identical'
                    % 'not' if not identical else ''
                )
                return True
        except FileNotFoundError:
            log.error(
                'Project file couldn\'t be located: %s', self.currentProject)
            return identical
        return False

    def saveProjectChanges(self) -> bool:
        '''Overwrites project file with autosave file'''
        try:
            os.remove(self.currentProject)  # type: ignore
            os.rename(self.autosavePath, self.currentProject)  # type: ignore
            return True
        except (FileNotFoundError, IsADirectoryError, TypeError) as e:
            self.showMessage(
                msg='Project file couldn\'t be saved.',
                detail=str(e))
            return False

    def openInputFileDialog(self) -> None:
        inputDir = self.settings.value("inputDir", os.path.expanduser("~"))

        fileName, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Audio File",
            self.inputDir, "Audio Files (%s)" % " ".join(Core.audioFormats))

        if fileName:
            self.settings.setValue("inputDir", os.path.dirname(fileName))
            self.lineEdit_audioFile.setText(fileName)

    def openOutputFileDialog(self) -> None:
        outputDir = self.settings.value("outputDir", os.path.expanduser("~"))

        fileName, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Set Output Video File",
            outputDir,
            "Video Files (%s);; All Files (*)" % " ".join(
                Core.videoFormats))

        if fileName:
            self.settings.setValue("outputDir", os.path.dirname(fileName))
            self.lineEdit_outputFile.setText(fileName)

    def stopVideo(self) -> None:
        log.info('Export cancelled')
        if hasattr(self, "videoWorker"):
            self.videoWorker.cancel()
        self.canceled = True

    def createAudioVisualization(self) -> None:
        # create output video if mandatory settings are filled in
        audioFile = self.lineEdit_audioFile.text()
        outputPath = self.lineEdit_outputFile.text()

        if not self._check_inputs(audioFile, outputPath):
            return

        self.canceled = False
        self.progressBarUpdated(-1)
        self.videoWorker = self.core.newVideoWorker(
            self, audioFile, outputPath
        )
        self.videoWorker.progressBarUpdate.connect(self.progressBarUpdated)
        self.videoWorker.progressBarSetText.connect(
            self.progressBarSetText)
        self.videoWorker.imageCreated.connect(self.showPreviewImage)
        self.videoWorker.encoding.connect(self.changeEncodingStatus)
        self.createVideo.emit()


    def _check_inputs(self, audioFile: str, outputPath: str) -> bool:
        """Checks if the input and output files are valid."""
        if not audioFile or not outputPath:
            self.showMessage(
                msg="You must select an audio file and output filename."
            )
            return False
        if not self.core.selectedComponents:
            self.showMessage(
                msg="Not enough components."
            )
            return False

        if not os.path.dirname(outputPath):
            outputPath = os.path.join(os.path.expanduser("~"), outputPath)
        if outputPath and os.path.isdir(outputPath):
            self.showMessage(
                msg='Chosen filename matches a directory, which '
                    'cannot be overwritten. Please choose a different '
                    'filename or move the directory.',
                icon='Warning',
            )
            return False
        return True


    @QtCore.pyqtSlot(str, str)
    def videoThreadError(self, msg: str, detail: str) -> None:
        try:
            self.stopVideo()
        except AttributeError as e:
            if 'videoWorker' not in str(e):
                raise
        self.showMessage(
            msg=msg,
            detail=detail,
            icon='Critical',
        )
        log.info('%s', repr(self))


    def changeEncodingStatus(self, status: bool) -> None:
        self.encoding = status
        if status:
            self._disable_encoding_ui()
        else:
            self._enable_encoding_ui()


    def _disable_encoding_ui(self) -> None:
        """Disables UI elements when encoding starts."""
        self.pushButton_createVideo.setEnabled(False)
        self.pushButton_Cancel.setEnabled(True)
        self.comboBox_resolution.setEnabled(False)
        self.stackedWidget.setEnabled(False)
        self.tab_encoderSettings.setEnabled(False)
        self.label_audioFile.setEnabled(False)
        self.toolButton_selectAudioFile.setEnabled(False)
        self.label_outputFile.setEnabled(False)
        self.toolButton_selectOutputFile.setEnabled(False)
        self.lineEdit_audioFile.setEnabled(False)
        self.lineEdit_outputFile.setEnabled(False)
        self.listWidget_componentList.setEnabled(False)
        self.pushButton_addComponent.setEnabled(False)
        self.pushButton_removeComponent.setEnabled(False)
        self.pushButton_listMoveDown.setEnabled(False)
        self.pushButton_listMoveUp.setEnabled(False)
        self.pushButton_undo.setEnabled(False)
        self.menuButton_newProject.setEnabled(False)
        self.menuButton_openProject.setEnabled(False)
        self.undoDialog.close()
        if sys.platform == 'darwin':
            self.progressLabel.setHidden(False)


    def _enable_encoding_ui(self) -> None:
        """Enables UI elements when encoding finishes/cancels."""
        self.pushButton_createVideo.setEnabled(True)
        self.pushButton_Cancel.setEnabled(False)
        self.comboBox_resolution.setEnabled(True)
        self.stackedWidget.setEnabled(True)
        self.tab_encoderSettings.setEnabled(True)
        self.label_audioFile.setEnabled(True)
        self.toolButton_selectAudioFile.setEnabled(True)
        self.lineEdit_audioFile.setEnabled(True)
        self.label_outputFile.setEnabled(True)
        self.toolButton_selectOutputFile.setEnabled(True)
        self.lineEdit_outputFile.setEnabled(True)
        self.pushButton_addComponent.setEnabled(True)
        self.pushButton_removeComponent.setEnabled(True)
        self.pushButton_listMoveDown.setEnabled(True)
        self.pushButton_listMoveUp.setEnabled(True)
        self.pushButton_undo.setEnabled(True)
        self.menuButton_newProject.setEnabled(True)
        self.menuButton_openProject.setEnabled(True)
        self.listWidget_componentList.setEnabled(True)
        self.progressLabel.setHidden(True)
        self.drawPreview(True)


    @QtCore.pyqtSlot(int)
    def progressBarUpdated(self, value: int) -> None:
        self.progressBar_createVideo.setValue(value)

    @QtCore.pyqtSlot(str)
    def progressBarSetText(self, value: str) -> None:
        if sys.platform == 'darwin':
            self.progressLabel.setText(value)
        else:
            self.progressBar_createVideo.setFormat(value)

    def updateResolution(self) -> None:
        resIndex: int = int(self.comboBox_resolution.currentIndex())
        res: List[str] = Core.resolutions[resIndex].split('x')
        changed: bool = res[0] != self.settings.value("outputWidth")
        self.settings.setValue('outputWidth', res[0])
        self.settings.setValue('outputHeight', res[1])
        if changed:
            for i in range(len(self.core.selectedComponents):
                self.core.updateComponent(i)

    def drawPreview(self, force: bool = False, **kwargs: Any) -> None:
        '''Use autosave keyword arg to force saving or not saving if needed'''
        self.newTask.emit(self.core.selectedComponents)
        # self.processTask.emit() # Removed as per the plan
        if force or 'autosave' in kwargs:
            if force or kwargs['autosave']:
                self.autosave(True)
        else:
            self.autosave()
        self.updateWindowTitle()

    @QtCore.pyqtSlot('QImage')
    def showPreviewImage(self, image: QtGui.QImage) -> None:
        self.previewWindow.changePixmap(image)

    @disableWhenEncoding
    def showUndoStack(self) -> None:
        self.undoDialog.show()

    def showFfmpegCommand(self) -> None:
        from textwrap import wrap
        from ..toolkit.ffmpeg import createFfmpegCommand
        command = createFfmpegCommand(
            self.lineEdit_audioFile.text(),
            self.lineEdit_outputFile.text(),
            self.core.selectedComponents
        )
        if command:
            command_str = " ".join(command)
            log.info(f"FFmpeg command: {command_str}")
            lines = wrap(command_str, 49)  # Wrap for display
            self.showMessage(
                msg=f"Current FFmpeg command:\n\n{' '.join(lines)}"
            )
        else:
            self.showMessage(msg="Could not generate FFmpeg command. See log for details.")

    def addComponent(self, compPos: int, moduleIndex: int) -> None:
        '''Creates an undoable action that adds a new component.'''
        action = AddComponent(self, compPos, moduleIndex)
        self.undoStack.push(action)

    def insertComponent(self, index: int) -> int:
        '''Triggered by Core to finish initializing a new component.'''
        componentList = self.listWidget_componentList
        stackedWidget = self.stackedWidget

        componentList.insertItem(
            index,
            self.core.selectedComponents[index].name)
        componentList.setCurrentRow(index)

        # connect to signal that adds an asterisk when modified
        self.core.selectedComponents[index].modified.connect(
            self.updateComponentTitle)

        self.pages.insert(index, self.core.selectedComponents[index].page)
        stackedWidget.insertWidget(index, self.pages[index])
        stackedWidget.setCurrentIndex(index)

        return index

    def removeComponent(self) -> None:
        componentList = self.listWidget_componentList
        selected = componentList.selectedItems()
        if selected:
            action = RemoveComponent(self, selected)
            self.undoStack.push(action)

    def _removeComponent(self, index: int) -> None:
        #The logic from removeComponent was extracted to this method, for easier use
        stackedWidget = self.stackedWidget
        componentList = self.listWidget_componentList
        stackedWidget.removeWidget(self.pages[index])
        componentList.takeItem(index)
        self.core.removeComponent(index)
        self.pages.pop(index)
        self.changeComponentWidget()
        self.drawPreview()

    @disableWhenEncoding
    def moveComponent(self, change: Union[int, str]) -> None:
        '''Moves a component relatively from its current position'''
        componentList = self.listWidget_componentList
        currentRow = componentList.currentRow()

        if currentRow == -1:  # No selection
            return

        if change == 'top':
            newRow = 0
        elif change == 'bottom':
            newRow = componentList.count() - 1
        else:
            newRow = currentRow + change

        if 0 <= newRow < componentList.count():
            action = MoveComponent(self, currentRow, newRow)
            self.undoStack.push(action)

    def getComponentListMousePos(self, position: QtCore.QPoint) -> int:
        '''
        Given a QPos, returns the component index under the mouse cursor
        or -1 if no component is there.
        '''
        componentList = self.listWidget_componentList

        # Iterate through items and check if the position is within their visual rect
        for i in range(componentList.count()):
            item = componentList.item(i)
            rect = componentList.visualItemRect(item)
            if rect.contains(position):
                return i  # Return the index of the item under the mouse

        return -1  # No item found at the given position


    @disableWhenEncoding
    def dragComponent(self, event: QtGui.QDropEvent) -> None:
        '''Used as Qt drop event for the component listwidget'''
        componentList = self.listWidget_componentList
        mousePos = self.getComponentListMousePos(event.pos())
        if mousePos > -1:
            change = (componentList.currentRow() - mousePos) * -1
        else:
            change = (componentList.count() - componentList.currentRow() - 1)
        self.moveComponent(change)

    def changeComponentWidget(self) -> None:
        selected = self.listWidget_componentList.selectedItems()
        if selected:
            index = self.listWidget_componentList.row(selected[0])
            self.stackedWidget.setCurrentIndex(index)

    def openPresetManager(self) -> None:
        '''Preset manager for importing, exporting, renaming, deleting'''
        self.presetManager.show_()

    def clear(self) -> None:
        '''Get a blank slate'''
        self.core.clearComponents()
        self.listWidget_componentList.clear()
        for widget in self.pages:
            self.stackedWidget.removeWidget(widget)
        self.pages = []
        for field in (
                self.lineEdit_audioFile,
                self.lineEdit_outputFile
                ):
            with blockSignals(field):
                field.setText('')
        self.progressBarUpdated(0)
        self.progressBarSetText('')
        self.undoStack.clear()

    @disableWhenEncoding
    def createNewProject(self, prompt: bool = True) -> None:
        if prompt:
            self.openSaveChangesDialog('starting a new project')

        self.clear()
        self.currentProject = None
        self.settings.setValue("currentProject", None)
        self.drawPreview(True)

    def saveCurrentProject(self) -> None:
        if self.currentProject:
            self.core.createProjectFile(self.currentProject, self)
            try:
                os.remove(self.autosavePath)
            except FileNotFoundError:
                pass
            self.updateWindowTitle()
        else:
            self.openSaveProjectDialog()

    def openSaveChangesDialog(self, phrase: str) -> None:
        success = True
        if self.autosaveExists(identical=False):
            ch = self.showMessage(
                msg="You have unsaved changes in project '%s'. "
                "Save before %s?" % (
                    os.path.basename(self.currentProject)[:-4],  # type: ignore
                    phrase
                ),
                showCancel=True)
            if ch:
                success = self.saveProjectChanges()

        if success and os.path.exists(self.autosavePath):
            os.remove(self.autosavePath)

    def openSaveProjectDialog(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Create Project File",
            self.settings.value("projectDir"),
            "Project Files (*.avp)")
        if not filename:
            return
        if not filename.endswith(".avp"):
            filename += '.avp'
        self.settings.setValue("projectDir", os.path.dirname(filename))
        self.settings.setValue("currentProject", filename)
        self.currentProject = filename
        self.core.createProjectFile(filename, self)
        self.updateWindowTitle()

    @disableWhenEncoding
    def openOpenProjectDialog(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Project File",
            self.settings.value("projectDir"),
            "Project Files (*.avp)")
        self.openProject(filename)

    def openProject(self, filepath: Optional[str], prompt: bool = True) -> None:
        if not filepath or not os.path.exists(filepath) \
                or not filepath.endswith('.avp'):
            return

        self.clear()
        # ask to save any changes that are about to get deleted
        if prompt:
            self.openSaveChangesDialog('opening another project')

        self.currentProject = filepath
        self.settings.setValue("currentProject", filepath)
        self.settings.setValue("projectDir", os.path.dirname(filepath))
        # actually load the project using core method
        self.core.openProject(self, filepath)
        self.drawPreview(autosave=False)
        self.updateWindowTitle()

    def showMessage(self,  msg: str,  detail: str = "", icon: str = 'Information', showCancel: bool = False, parent: Optional[QtWidgets.QWidget] = None) -> bool:

        if parent is None:
            parent = self
        msg_box = QtWidgets.QMessageBox(parent)
        msg_box.setWindowTitle(appName)
        msg_box.setModal(True)
        msg_box.setText(msg)
        msg_box.setIcon(
            eval('QtWidgets.QMessageBox.%s' % icon)
            if icon else QtWidgets.QMessageBox.Information
        )
        msg_box.setDetailedText(detail if detail else None)
        if showCancel:
            msg_box.setStandardButtons(
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel)
        else:
            msg_box.setStandardButtons(QtWidgets.QMessageBox.Ok)
        ch = msg_box.exec_()
        if ch == 1024:
            return True
        return False

    @disableWhenEncoding
    def componentContextMenu(self, QPos: QtCore.QPoint) -> None:
        '''Appears when right-clicking the component list'''
        componentList = self.listWidget_componentList
        self.menu: QtWidgets.QMenu = QtWidgets.QMenu()  # Type hint
        parentPosition = componentList.mapToGlobal(QtCore.QPoint(0, 0))

        index = self.getComponentListMousePos(QPos)
        if index > -1:
            # Show preset menu if clicking a component
            self.presetManager.findPresets()
            menuItem = self.menu.addAction("Save Preset")
            menuItem.triggered.connect(
                self.presetManager.openSavePresetDialog
            )

            # submenu for opening presets
            try:
                presets = self.presetManager.presets[
                    str(self.core.selectedComponents[index])
                ]
                self.presetSubmenu: QtWidgets.QMenu = QtWidgets.QMenu("Open Preset")  # Type hint
                self.menu.addMenu(self.presetSubmenu)

                for version, presetName in presets:
                    menuItem = self.presetSubmenu.addAction(presetName)
                    menuItem.triggered.connect(
                        lambda _, presetName=presetName:
                            self.presetManager.openPreset(presetName)
                    )
            except KeyError:
                pass

            if self.core.selectedComponents[index].currentPreset:
                menuItem = self.menu.addAction("Clear Preset")
                menuItem.triggered.connect(
                    self.presetManager.clearPreset
                )
            self.menu.addSeparator()

        # "Add Component" submenu
        self.submenu: QtWidgets.QMenu = QtWidgets.QMenu("Add")  # Type hint
        self.menu.addMenu(self.submenu)
        insertCompAtTop = self.settings.value("pref_insertCompAtTop")
        for i, comp in enumerate(self.core.modules):
            menuItem = self.submenu.addAction(comp.Component.name)
            menuItem.triggered.connect(
                lambda _, item=i: self.addComponent(
                    0 if insertCompAtTop else index, item
                )
            )

        self.menu.move(parentPosition + QPos)
        self.menu.show()