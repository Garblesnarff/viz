'''
    Base classes for components to import. Read comments for some documentation
    on making a valid component.
'''
from PyQt5 import uic, QtCore, QtWidgets
from PyQt5.QtGui import QColor
import os
import sys
import math
import time
import logging
from copy import copy
from typing import List, Dict, Any, Tuple, Union, Callable, Optional, Type, TYPE_CHECKING

from .toolkit.frame import BlankFrame
from .toolkit import (
    getWidgetValue, setWidgetValue, connectWidget, rgbFromString, blockSignals
)

if TYPE_CHECKING:
    from .core import Core  # Avoid circular import

log = logging.getLogger('AVP.ComponentHandler')

# Type alias for easier type hinting
WidgetDict = Dict[str, List[QtWidgets.QWidget]]

class ComponentMetaclass(type(QtCore.QObject)):  # Inherit from type, and specify metaclass for QObject
    '''
        Checks the validity of each Component class and mutates some attrs.
        E.g., takes only major version from version string & decorates methods
    '''

    def initializationWrapper(func: Callable) -> Callable:  # Type hints for decorators
        def initializationWrapper(self: 'Component', *args: Any, **kwargs: Any) -> Any: # Added self
            try:
                return func(self, *args, **kwargs)
            except Exception:
                try:
                    raise ComponentError(self, 'initialization process')
                except ComponentError:
                    return
        return initializationWrapper

    def renderWrapper(func: Callable) -> Callable:
        def renderWrapper(self: 'Component', *args: Any, **kwargs: Any) -> Any: # Added self and return
            try:
                log.verbose(
                    '### %s #%s renders a preview frame ###',
                    self.__class__.name, str(self.compPos),
                )
                return func(self, *args, **kwargs)
            except Exception as e:
                try:
                    if e.__class__.__name__.startswith('Component'):
                        raise
                    else:
                        raise ComponentError(self, 'renderer')
                except ComponentError:
                    return BlankFrame()  # Return a blank frame on error
        return renderWrapper

    def commandWrapper(func: Callable) -> Callable:
        '''Intercepts the command() method to check for global args'''
        def commandWrapper(self: 'Component', arg: str) -> Any: # Added self and return
            if arg.startswith('preset='):
                _, preset = arg.split('=', 1)
                path = os.path.join(self.core.getPresetDir(self), preset)
                if not os.path.exists(path):
                    print('Couldn\'t locate preset "%s"' % preset)
                    #Consider raising an exception, for consistency
                    return
                else:
                    print('Opening "%s" preset on layer %s' % (
                        preset, self.compPos)
                    )
                    self.core.openPreset(path, self.compPos, preset)
                    # Don't call the component's command() method
                    return
            else:
                return func(self, arg)
        return commandWrapper

    def propertiesWrapper(func: Callable) -> Callable:
        '''Intercepts the usual properties if the properties are locked.'''
        def propertiesWrapper(self: 'Component') -> List[str]:  # Added self, and return is a list of strings
            if self._lockedProperties is not None:
                return self._lockedProperties
            else:
                try:
                    return func(self)
                except Exception:
                    try:
                        raise ComponentError(self, 'properties')
                    except ComponentError:
                        return []  # Return empty list on error
        return propertiesWrapper

    def errorWrapper(func: Callable) -> Callable:
        '''Intercepts the usual error message if it is locked.'''
        def errorWrapper(self: 'Component') -> Optional[Union[str, Tuple[str, str]]]:  # Added self and return type
            if self._lockedError is not None:
                return self._lockedError
            else:
                return func(self)
        return errorWrapper

    def loadPresetWrapper(func: Callable) -> Callable:
        '''Wraps loadPreset to handle the self.openingPreset boolean'''
        class openingPreset:  # Inner class for context manager
            def __init__(self, comp: 'Component') -> None:
                self.comp = comp

            def __enter__(self) -> None:
                self.comp.openingPreset = True

            def __exit__(self, *args: Any) -> None:
                self.comp.openingPreset = False

        def presetWrapper(self: 'Component', *args: Any) -> Any: # Added self
            with openingPreset(self):
                try:
                    return func(self, *args)
                except Exception:
                    try:
                        raise ComponentError(self, 'preset loader')
                    except ComponentError:
                        return
        return presetWrapper

    def updateWrapper(func: Callable) -> Callable:
        '''
            Calls _preUpdate before every subclass update().
            Afterwards, for non-user updates, calls _autoUpdate().
            For undoable updates triggered by the user, calls _userUpdate()
        '''
        class wrap: # Inner class for context manager
            def __init__(self, comp: 'Component', auto: bool) -> None:
                self.comp = comp
                self.auto = auto

            def __enter__(self) -> None:
                self.comp._preUpdate()

            def __exit__(self, *args: Any) -> None:
                if self.auto or self.comp.openingPreset \
                        or not hasattr(self.comp.parent, 'undoStack'):
                    log.verbose('Automatic update')
                    self.comp._autoUpdate()
                else:
                    log.verbose('User update')
                    self.comp._userUpdate()

        def updateWrapper(self: 'Component', **kwargs: Any) -> Any: # Added self
            auto = kwargs.get('auto', False)  # Use .get() for optional kwargs
            with wrap(self, auto):
                try:
                    return func(self)
                except Exception:
                    try:
                        raise ComponentError(self, 'update method')
                    except ComponentError:
                        return
        return updateWrapper

    def widgetWrapper(func: Callable) -> Callable:
        '''Connects all widgets to update method after the subclass's method'''
        class wrap:  # Inner class for context manager
            def __init__(self, comp: 'Component') -> None:
                self.comp = comp

            def __enter__(self) -> None:
                pass

            def __exit__(self, *args: Any) -> None:
                for widgetList in self.comp._allWidgets.values():
                    for widget in widgetList:
                        log.verbose('Connecting %s', str(
                            widget.__class__.__name__))
                        connectWidget(widget, self.comp.update)

        def widgetWrapper(self: 'Component', *args: Any, **kwargs: Any) -> Any: # Added self
            auto = kwargs.get('auto', False)
            with wrap(self):
                try:
                    return func(self, *args, **kwargs)
                except Exception:
                    try:
                        raise ComponentError(self, 'widget creation')
                    except ComponentError:
                        return
        return widgetWrapper

    def __new__(cls, name: str, parents: Tuple[type, ...], attrs: Dict[str, Any]) -> Any: # Added return
        if 'ui' not in attrs:
            # Use module name as ui filename by default
            attrs['ui'] = '%s.ui' % os.path.splitext(
                    attrs['__module__'].split('.')[-1]
                )[0]

        decorate = (
            'names',                            # Class methods
            'error', 'audio', 'properties',     # Properties
            'preFrameRender', 'previewRender',
            'loadPreset', 'command',
            'update', 'widget',
        )

        # Auto-decorate methods
        for key in decorate:
            if key not in attrs:
                continue
            if key in ('names'):
                attrs[key] = classmethod(attrs[key])
            elif key in ('audio'):
                attrs[key] = property(attrs[key])
            elif key == 'command':
                attrs[key] = cls.commandWrapper(attrs[key])
            elif key == 'previewRender':
                attrs[key] = cls.renderWrapper(attrs[key])
            elif key == 'preFrameRender':
                attrs[key] = cls.initializationWrapper(attrs[key])
            elif key == 'properties':
                attrs[key] = cls.propertiesWrapper(attrs[key])
            elif key == 'error':
                attrs[key] = cls.errorWrapper(attrs[key])
            elif key == 'loadPreset':
                attrs[key] = cls.loadPresetWrapper(attrs[key])
            elif key == 'update':
                attrs[key] = cls.updateWrapper(attrs[key])
            elif key == 'widget' and parents[0] != QtCore.QObject:
                attrs[key] = cls.widgetWrapper(attrs[key])

        # Turn version string into a number
        try:
            if 'version' not in attrs:
                log.error(
                    'No version attribute in %s. Defaulting to 1',
                    attrs['name'])
                attrs['version'] = 1
            else:
                attrs['version'] = int(attrs['version'].split('.')[0])
        except ValueError:
            log.critical(
                '%s component has an invalid version string:\n%s',
                attrs['name'], str(attrs['version'])
            )
        except KeyError:
            log.critical('%s component has no version string.', attrs['name'])
        else:
            return super().__new__(cls, name, parents, attrs)
        quit(1) # If an exception happens, quit.


class Component(QtCore.QObject, metaclass=ComponentMetaclass):
    '''
        The base class for components to inherit.
    '''

    name: str = 'Component'
    # ui = 'name_Of_Non_Default_Ui_File'

    version: str = '1.0.0'
    # The major version (before the first dot) is used to determine
    # preset compatibility; the rest is ignored so it can be non-numeric.

    modified = QtCore.pyqtSignal(int, dict)
    _error = QtCore.pyqtSignal(str, str)

    def __init__(self, moduleIndex: int, compPos: int, core: 'Core') -> None:
        super().__init__()
        self.moduleIndex: int = moduleIndex
        self.compPos: int = compPos
        self.core: 'Core' = core # Added type hint

        # STATUS VARIABLES
        self.currentPreset: Optional[str] = None
        self._allWidgets: WidgetDict = {}
        self._trackedWidgets: Dict[str, QtWidgets.QWidget] = {}
        self._presetNames: Dict[str, str] = {}
        self._commandArgs: Dict[str, Optional[str]] = {}
        self._colorWidgets: Dict[str, QtWidgets.QPushButton] = {}
        self._colorFuncs: Dict[str, Callable[[], None]] = {}
        self._relativeWidgets: Dict[str, QtWidgets.QWidget] = {}
        # Pixel values stored as floats
        self._relativeValues: Dict[str, float] = {}
        # Maximum values of spinBoxes at 1080p (Core.resolutions[0])
        self._relativeMaximums: Dict[str, int] = {}

        # LOCKING VARIABLES
        self.openingPreset: bool = False
        self.mergeUndo: bool = True
        self._lockedProperties: Optional[List[str]] = None
        self._lockedError: Optional[Union[str, Tuple[str, str]]] = None
        self._lockedSize: Optional[Tuple[int, int]] = None
        # If set to a dict, values are used as basis to update relative widgets
        self.oldAttrs: Optional[Dict[str, Any]] = None
        # Stop lengthy processes in response to this variable
        self.canceled: bool = False

    def __str__(self) -> str:
        return self.__class__.name

    def __repr__(self) -> str:
        import pprint
        try:
            preset = self.savePreset()
        except Exception as e:
            preset = '%s occurred while saving preset' % str(e)

        return (
            'Component(module %s, pos %s) (%s)\n'
            'Name: %s v%s\nPreset: %s' % (
                self.moduleIndex, self.compPos,
                object.__repr__(self),
                self.__class__.name, str(self.__class__.version),
                pprint.pformat(preset)
            )
        )

    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
    # Render Methods
    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~

    def previewRender(self) -> Image.Image:
        image = BlankFrame(self.width, self.height)
        return image

    def preFrameRender(self, **kwargs: Any) -> None:
        '''
            Must call super() when subclassing
            Triggered only before a video is exported (video_thread.py)
                self.audioFile = filepath to the main input audio file
                self.completeAudioArray = a list of audio samples
                self.sampleSize = number of audio samples per video frame
                self.progressBarUpdate = signal to set progress bar number
                self.progressBarSetText = signal to set progress bar text
            Use the latter two signals to update the MainWindow if needed
            for a long initialization procedure (i.e., for a visualizer)
        '''
        for key, value in kwargs.items():
            setattr(self, key, value)

    def frameRender(self, frameNo: int) -> Image.Image:
        audioArrayIndex = frameNo * self.sampleSize
        image = BlankFrame(self.width, self.height)
        return image

    def postFrameRender(self) -> None:
        pass
    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
    # Properties
    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~

    def properties(self) -> List[str]:
        '''
            Return a list of properties to signify if your component is
            non-animated ('static'), returns sound ('audio'), or has
            encountered an error in configuration ('error').
        '''
        return []

    def error(self) -> Optional[Union[str, Tuple[str, str]]]:
        '''
            Return a string containing an error message, or None for a default.
            Or tuple of two strings for a message with details.
            Alternatively use lockError(msgString) within properties()
            to skip this method entirely.
        '''
        return None

    def audio(self) -> Optional[Tuple[Union[str, Any], Dict[str, str]]]:
        '''
            Return audio to mix into master as a tuple with two elements:
            The first element can be:
                - A string (path to audio file),
                - Or an object that returns audio data through a pipe
            The second element must be a dictionary of ffmpeg filters/options
            to apply to the input stream. See the filter docs for ideas:
            https://ffmpeg.org/ffmpeg-filters.html
        '''
        return None

    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
    # Idle Methods
    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~

    def widget(self, parent: Any) -> None:
        '''
            Call super().widget(*args) to create the component widget
            which also auto-connects any common widgets (e.g., checkBoxes)
            to self.update(). Then in a subclass connect special actions
            (e.g., pushButtons to select a file) and initialize
        '''
        self.parent = parent
        self.settings = parent.settings
        log.verbose(
            'Creating UI for %s #%s\'s widget',
            self.__class__.name, self.compPos
        )
        self.page = self.loadUi(self.__class__.ui)

        # Find all normal widgets which will be connected after subclass method
        self._allWidgets = {
            'lineEdit': self.page.findChildren(QtWidgets.QLineEdit),
            'checkBox': self.page.findChildren(QtWidgets.QCheckBox),
            'spinBox': self.page.findChildren(QtWidgets.QSpinBox),
            'comboBox': self.page.findChildren(QtWidgets.QComboBox),
        }
        self._allWidgets['spinBox'].extend(
            self.page.findChildren(QtWidgets.QDoubleSpinBox)
        )

    def update(self) -> None:
        '''
            Starting point for a component update. A subclass should override
            this method, and the base class will then magically insert a call
            to either _autoUpdate() or _userUpdate() at the end.
        '''
        pass #The subclass should implement this

    def loadPreset(self, presetDict: Dict[str, Any], presetName: Optional[str] = None) -> None:
        '''
            Subclasses should take (presetDict, *args) as args.
            Must use super().loadPreset(presetDict, *args) first,
            then update self.page widgets using the preset dict.
        '''
        self.currentPreset = presetName \
            if presetName is not None else presetDict['preset']
        for attr, widget in self._trackedWidgets.items():
            key = attr if attr not in self._presetNames \
                else self._presetNames[attr]
            try:
                val = presetDict[key]
            except KeyError as e:
                log.info(
                    '%s missing value %s. Outdated preset?',
                    self.currentPreset, str(e)
                )
                val = getattr(self, key)

            if attr in self._colorWidgets:
                widget.setText('%s,%s,%s' % val)
                btnStyle = (
                    "QPushButton { background-color : %s; outline: none; }"
                    % QColor(*val).name()
                )
                self._colorWidgets[attr].setStyleSheet(btnStyle)
            elif attr in self._relativeWidgets:
                self._relativeValues[attr] = val
                pixelVal = self.pixelValForAttr(attr, val)
                setWidgetValue(widget, pixelVal)
            else:
                setWidgetValue(widget, val)

    def savePreset(self) -> Dict[str, Any]:
        saveValueStore: Dict[str, Any] = {}
        for attr, widget in self._trackedWidgets.items():
            presetAttrName = (
                attr if attr not in self._presetNames
                else self._presetNames[attr]
            )
            if attr in self._relativeWidgets:
                try:
                    val: Union[str, Tuple[int, int, int], float, int, bool] = self._relativeValues[attr]
                except AttributeError:
                    val = self.floatValForAttr(attr)
            else:
                val = getattr(self, attr)

            saveValueStore[presetAttrName] = val
        return saveValueStore

    def commandHelp(self) -> None:
        '''Help text as string for this component's commandline arguments'''
        pass #The component should implement this if they want to add extra commands

    def command(self, arg: str = '') -> None:
        '''
            Configure a component using an arg from the commandline. This is
            never called if global args like 'preset=' are found in the arg.
            So simply check for any non-global args in your component and
            call super().command() at the end to get a Help message.
        '''
        print(
            self.__class__.name, 'Usage:\n'
            'Open a preset for this component:\n'
            '    "preset=Preset Name"'
        )
        self.commandHelp()
        quit(0)

    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
    # "Private" Methods
    # =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
    def _preUpdate(self) -> None:
        '''Happens before subclass update()'''
        for attr in self._relativeWidgets:
            self.updateRelativeWidget(attr)

    def _userUpdate(self) -> None:
        '''Happens after subclass update() for an undoable update by user.'''
        oldWidgetVals: Dict[str, Any] = {
            attr: copy(getattr(self, attr))
            for attr in self._trackedWidgets
        }
        newWidgetVals: Dict[str, Any] = {
            attr: getWidgetValue(widget)
            if attr not in self._colorWidgets else rgbFromString(widget.text())
            for attr, widget in self._trackedWidgets.items()
        }
        modifiedWidgets: Dict[str, Any] = {
            attr: val
            for attr, val in newWidgetVals.items()
            if val != oldWidgetVals[attr]
        }
        if modifiedWidgets:
            action = ComponentUpdate(self, oldWidgetVals, modifiedWidgets)
            if hasattr(self.parent, 'undoStack'):
                self.parent.undoStack.push(action)
            else:
                log.warning('Undo action was triggered, but could not be added to a missing undo stack.')

    def _autoUpdate(self) -> None:
        '''Happens after subclass update() for an internal component update.'''
        newWidgetVals: Dict[str, Any] = {
            attr: getWidgetValue(widget)
            for attr, widget in self._trackedWidgets.items()
        }
        self.setAttrs(newWidgetVals)
        self._sendUpdateSignal()

    def setAttrs(self, attrDict: Dict[str, Any]) -> None:
        '''
            Sets attrs (linked to trackedWidgets) in this component to
            the values in the attrDict. Mutates certain widget values if needed
        '''
        for attr, val in attrDict.items():
            if attr in self._colorWidgets:
                # Color Widgets must have a tuple & have a button to update
                if type(val) is tuple:
                    rgbTuple = val
                else:
                    rgbTuple = rgbFromString(val)
                btnStyle = (
                    "QPushButton { background-color : %s; outline: none; }"
                    % QColor(*rgbTuple).name())
                self._colorWidgets[attr].setStyleSheet(btnStyle)
                setattr(self, attr, rgbTuple)

            else:
                # Normal tracked widget
                setattr(self, attr, val)
            log.verbose('Setting %s self.%s to %s' % (
                self.__class__.name, attr, val))

    def setWidgetValues(self, attrDict: Dict[str, Any]) -> None:
        '''
            Sets widgets defined by keys in trackedWidgets in this preset to
            the values in the attrDict.
        '''
        affectedWidgets = [
            self._trackedWidgets[attr] for attr in attrDict
        ]
        with blockSignals(affectedWidgets):
            for attr, val in attrDict.items():
                widget = self._trackedWidgets[attr]
                if attr in self._colorWidgets:
                    val = '%s,%s,%s' % val
                setWidgetValue(widget, val)

    def _sendUpdateSignal(self) -> None:
        if not self.core.openingProject:
            self.parent.drawPreview()
            saveValueStore = self.savePreset()
            saveValueStore['preset'] = self.currentPreset
            self.modified.emit(self.compPos, saveValueStore)

    def trackWidgets(self, trackDict: Dict[str, QtWidgets.QWidget], **kwargs: Any) -> None:
        '''
            Name widgets to track in update(), savePreset(), loadPreset(), and
            command(). Requires a dict of attr names as keys, widgets as values

            Optional args:
                'presetNames': preset variable names to replace attr names
                'commandArgs': arg keywords that differ from attr names
                'colorWidgets': identify attr as RGB tuple & update button CSS
                'relativeWidgets': change value proportionally to resolution

            NOTE: Any kwarg key set to None will selectively disable tracking.
        '''
        self._trackedWidgets = trackDict
        for kwarg in kwargs:
            try:
                if kwarg in (
                        'presetNames',
                        'commandArgs',
                        'colorWidgets',
                        'relativeWidgets',
                        ):
                    setattr(self, '_{}'.format(kwarg), kwargs[kwarg])
                else:
                    raise ComponentError(
                        self, 'Nonsensical keywords to trackWidgets.')
            except ComponentError:
                continue

            if kwarg == 'colorWidgets':
                def makeColorFunc(attr: str) -> Callable[[], None]:
                    def pickColor_() -> None:
                        self.mergeUndo = False
                        self.pickColor(
                            self._trackedWidgets[attr],
                            self._colorWidgets[attr]
                        )
                        self.mergeUndo = True
                    return pickColor_
                self._colorFuncs = {
                    attr: makeColorFunc(attr) for attr in kwargs[kwarg]
                }
                for attr, func in self._colorFuncs.items():
                    self._colorWidgets[attr].clicked.connect(func)
                    self._colorWidgets[attr].setStyleSheet(
                        "QPushButton {"
                        "background-color : #FFFFFF; outline: none; }"
                    )

            if kwarg == 'relativeWidgets':
                # store maximum values of spinBoxes to be scaled appropriately
                for attr in kwargs[kwarg]:
                    self._relativeMaximums[attr] = \
                            self._trackedWidgets[attr].maximum()
                    self.updateRelativeWidgetMaximum(attr)
                    setattr(
                        self, attr, getWidgetValue(self._trackedWidgets[attr])
                    )

        self._preUpdate()
        self._autoUpdate()

    def pickColor(self, textWidget: QtWidgets.QLineEdit, button: QtWidgets.QPushButton) -> None:
        '''Use color picker to get color input from the user.'''
        dialog = QtWidgets.QColorDialog()
        dialog.setOption(QtWidgets.QColorDialog.ShowAlphaChannel, True)
        color = dialog.getColor()
        if color.isValid():
            RGBstring = '%s,%s,%s' % (
                str(color.red()), str(color.green()), str(color.blue()))
            btnStyle = "QPushButton{background-color: %s; outline: none;}" \
                % color.name()
            textWidget.setText(RGBstring)
            button.setStyleSheet(btnStyle)

    def lockProperties(self, propList: List[str]) -> None:
        self._lockedProperties = propList

    def lockError(self, msg: str) -> None:
        self._lockedError = msg

    def lockSize(self, w: int, h: int) -> None:
        self._lockedSize = (w, h)

    def unlockProperties(self) -> None:
        self._lockedProperties = None

    def unlockError(self) -> None:
        self._lockedError = None

    def unlockSize(self) -> None:
        self._lockedSize = None

    def loadUi(self, filename: str) -> QtWidgets.QWidget:
        '''Load a Qt Designer ui file to use for this component's widget'''
        return uic.loadUi(os.path.join(self.core.componentsPath, filename))

    @property
    def width(self) -> int:
        if self._lockedSize is None:
            return int(self.settings.value('outputWidth'))
        else:
            return self._lockedSize[0]

    @property
    def height(self) -> int:
        if self._lockedSize is None:
            return int(self.settings.value('outputHeight'))
        else:
            return self._lockedSize[1]

    def cancel(self) -> None:
        '''Stop any lengthy process in response to this variable.'''
        self.canceled = True

    def reset(self) -> None:
        self.canceled = False
        self.unlockProperties()
        self.unlockError()
    def relativeWidgetAxis(func: Callable) -> Callable:
        def relativeWidgetAxis(self: 'Component', attr: str, *args: Any, **kwargs: Any) -> Any:
            hasVerticalWords = (
                lambda attr:
                'height' in attr.lower() or
                'ypos' in attr.lower() or
                attr == 'y'
            )
            if 'axis' not in kwargs:
                axis = self.width
                if hasVerticalWords(attr):
                    axis = self.height
                kwargs['axis'] = axis
            if 'axis' in kwargs and type(kwargs['axis']) is tuple:
                axis = kwargs['axis'][0]
                if hasVerticalWords(attr):
                    axis = kwargs['axis'][1]
                kwargs['axis'] = axis
            return func(self, attr, *args, **kwargs)
        return relativeWidgetAxis

    @relativeWidgetAxis
    def pixelValForAttr(self, attr: str, val: Optional[float] = None, **kwargs: Any) -> int:
        if val is None:
            val = self._relativeValues[attr]
        if val > 50.0:
            log.warning(
                '%s #%s attempted to set %s to dangerously high number %s',
                self.__class__.name, self.compPos, attr, val
            )
            val = 50.0
        result = math.ceil(kwargs['axis'] * val)
        log.verbose(
            'Converting %s: f%s to px%s using axis %s',
            attr, val, result, kwargs['axis']
        )
        return result

    @relativeWidgetAxis
    def floatValForAttr(self, attr: str, val: Optional[int] = None, **kwargs: Any) -> float:
        if val is None:
            val = self._trackedWidgets[attr].value()
        return val / kwargs['axis']

    def setRelativeWidget(self, attr: str, floatVal: float) -> None:
        '''Set a relative widget using a float'''
        pixelVal = self.pixelValForAttr(attr, floatVal)
        with blockSignals([self._trackedWidgets[attr]]):
            self._trackedWidgets[attr].setValue(pixelVal)
        self.update(auto=True)

    def getOldAttr(self, attr: str) -> Any:
        '''
            Returns previous state of this attr. Used to determine whether
            a relative widget must be updated. Required because undoing/redoing
            can make determining the 'previous' value tricky.
        '''
        if self.oldAttrs is not None:
            return self.oldAttrs[attr]
        else:
            try:
                return getattr(self, attr)
            except AttributeError:
                log.error('Using visible values instead of oldAttrs')
                return self._trackedWidgets[attr].value()

    def updateRelativeWidget(self, attr: str) -> None:
        '''Called by _preUpdate() for each relativeWidget before each update'''
        oldUserValue = self.getOldAttr(attr)
        newUserValue = self._trackedWidgets[attr].value()
        newRelativeVal = self.floatValForAttr(attr, newUserValue)

        if attr in self._relativeValues:
            oldRelativeVal = self._relativeValues[attr]
            if oldUserValue == newUserValue \
                    and oldRelativeVal != newRelativeVal:
                # Float changed without pixel value changing, which
                # means the pixel value needs to be updated
                log.debug(
                    'Updating %s #%s\'s relative widget: %s',
                    self.__class__.name, self.compPos, attr)
                with blockSignals([self._trackedWidgets[attr]]):
                    self.updateRelativeWidgetMaximum(attr)
                    pixelVal = self.pixelValForAttr(attr, oldRelativeVal)
                    self._trackedWidgets[attr].setValue(pixelVal)

        if attr not in self._relativeValues \
                or oldUserValue != newUserValue:
            self._relativeValues[attr] = newRelativeVal

    def updateRelativeWidgetMaximum(self, attr: str) -> None:
        maxRes = int(self.core.resolutions[0].split('x')[0])
        newMaximumValue = self.width * (
            self._relativeMaximums[attr] /
            maxRes
        )
        self._trackedWidgets[attr].setMaximum(int(newMaximumValue))


class ComponentError(RuntimeError):
    '''Gives the MainWindow a traceback to display, and cancels the export.'''

    prevErrors: List[str] = []
    lastTime: float = time.time()

    def __init__(self, caller: Component, name: str, msg: Optional[str] = None) -> None:
        if msg is None and sys.exc_info()[0] is not None:
            msg = str(sys.exc_info()[1])  # type: ignore
        else:
            msg = 'Unknown error.'
        log.error("ComponentError by %s's %s: %s" % (
            caller.name, name, msg))

        # Don't create multiple windows for quickly repeated messages
        if len(ComponentError.prevErrors) > 1:
            ComponentError.prevErrors.pop()
        ComponentError.prevErrors.insert(0, name)
        curTime = time.time()
        if name in ComponentError.prevErrors[1:] \
                and curTime - ComponentError.lastTime < 1.0:
            return
        ComponentError.lastTime = time.time()

        from .toolkit import formatTraceback
        if sys.exc_info()[0] is not None:
            string = (
                "%s component (#%s): %s encountered %s %s: %s" % (
                    caller.__class__.name,
                    str(caller.compPos),
                    name,
                    'an' if any([
                        sys.exc_info()[0].__name__.startswith(vowel)  # type: ignore
                        for vowel in ('A', 'I', 'U', 'O', 'E')
                    ]) else 'a',
                    sys.exc_info()[0].__name__,  # type: ignore
                    str(sys.exc_info()[1])  # type: ignore
                )
            )
            detail = formatTraceback(sys.exc_info()[2])  # type: ignore
        else:
            string = name
            detail = "Attributes:\n%s" % (
                "\n".join(
                    [m for m in dir(caller) if not m.startswith('_')]
                )
            )

        super().__init__(string)
        caller.lockError(string)
        caller._error.emit(string, detail)


class ComponentUpdate(QtWidgets.QUndoCommand):
    '''Command object for making a component action undoable'''
    def __init__(self, parent: Component, oldWidgetVals: Dict[str, Any], modifiedVals: Dict[str, Any]) -> None:
        super().__init__(
            'change %s component #%s' % (
                parent.name, parent.compPos
            )
        )
        self.undone: bool = False
        self.res: Tuple[int, int] = (int(parent.width), int(parent.height))
        self.parent: Component = parent
        self.oldWidgetVals: Dict[str, Any] = {
            attr: copy(val)
            if attr not in self.parent._relativeWidgets
            else self.parent.floatValForAttr(attr, val, axis=self.res)
            for attr, val in oldWidgetVals.items()
            if attr in modifiedVals
        }
        self.modifiedVals: Dict[str, Any] = {
            attr: val
            if attr not in self.parent._relativeWidgets
            else self.parent.floatValForAttr(attr, val, axis=self.res)
            for attr, val in modifiedVals.items()
        }

        # Because relative widgets change themselves every update based on
        # their previous value, we must store ALL their values in case of undo
        self.relativeWidgetValsAfterUndo: Dict[str, Any] = {
            attr: copy(getattr(self.parent, attr))
            for attr in self.parent._relativeWidgets
        }

        # Determine if this update is mergeable
        self.id_: int = -1
        if len(self.modifiedVals) == 1 and self.parent.mergeUndo:
            attr, val = self.modifiedVals.popitem()
            self.id_ = sum([ord(letter) for letter in attr[-14:]])
            self.modifiedVals[attr] = val
        else:
            log.warning(
                '%s component settings changed at once. (%s)',
                len(self.modifiedVals), repr(self.modifiedVals)
            )

    def id(self) -> int:
        '''If 2 consecutive updates have same id, Qt will call mergeWith()'''
        return self.id_

    def mergeWith(self, other: 'ComponentUpdate') -> bool:
        self.modifiedVals.update(other.modifiedVals)
        return True

    def setWidgetValues(self, attrDict: Dict[str, Any]) -> None:
        '''
            Mask the component's usual method to handle our
            relative widgets in case the resolution has changed.
        '''
        newAttrDict = {
            attr: val if attr not in self.parent._relativeWidgets
            else self.parent.pixelValForAttr(attr, val)
            for attr, val in attrDict.items()
        }
        self.parent.setWidgetValues(newAttrDict)

    def redo(self) -> None:
        if self.undone:
            log.info('Redoing component update')
        self.parent.oldAttrs = self.relativeWidgetValsAfterUndo
        self.setWidgetValues(self.modifiedVals)
        self.parent.update(auto=True)
        self.parent.oldAttrs = None
        if not self.undone:
            self.relativeWidgetValsAfterRedo = {
                attr: copy(getattr(self.parent, attr))
                for attr in self.parent._relativeWidgets
            }
            self.parent._sendUpdateSignal()

    def undo(self) -> None:
        log.info('Undoing component update')
        self.undone = True
        self.parent.oldAttrs = self.relativeWidgetValsAfterRedo
        self.setWidgetValues(self.oldWidgetVals)
        self.parent.update(auto=True)
        self.parent.oldAttrs = None