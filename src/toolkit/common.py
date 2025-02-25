'''
    Common functions
'''
from PyQt5 import QtWidgets
import string
import os
import sys
import subprocess
import logging
from copy import copy
from collections import OrderedDict
from typing import List, Dict, Any, Union, Callable, TypeVar, Sequence, overload

log = logging.getLogger('AVP.Toolkit.Common')

# Type alias for a widget or a list of widgets.  Makes type hints shorter.
WidgetOrSequence = Union[QtWidgets.QWidget, Sequence[QtWidgets.QWidget]]

class blockSignals:
    '''
        Context manager to temporarily block list of QtWidgets from updating,
        and guarantee restoring the previous state afterwards.
    '''
    def __init__(self, widgets: WidgetOrSequence) -> None:
        if isinstance(widgets, dict):
            self.widgets = concatDictVals(widgets)
        else:
            self.widgets: Sequence[QtWidgets.QWidget] = (
                widgets if isinstance(widgets, Sequence)
                else [widgets]
            )
        self.oldStates: List[bool] = [] # Store outside the loop

    def __enter__(self) -> None:
        log.verbose(
            'Blocking signals for %s',
            ", ".join([
                str(w.__class__.__name__) for w in self.widgets
            ])
        )
        self.oldStates = [w.signalsBlocked() for w in self.widgets] # Capture *before* blocking
        for w in self.widgets:
            w.blockSignals(True)


    def __exit__(self, *args: Any) -> None:
        log.verbose(
            'Resetting blockSignals to original states'
        )
        for w, state in zip(self.widgets, self.oldStates):
            w.blockSignals(state)


def concatDictVals(d: Dict[Any, Union[Any, List[Any]]]) -> List[Any]:
    '''Concatenates all values in given dict into one list.'''
    # Convert all values to lists, then flatten the list of lists.
    return [item for sublist in d.values() for item in (sublist if isinstance(sublist, list) else [sublist])]



def badName(name: str) -> bool:
    '''Returns whether a name contains non-alphanumeric chars'''
    return any([letter in string.punctuation for letter in name])


def alphabetizeDict(dictionary: Dict[Any, Any]) -> Dict[Any, Any]:
    '''Alphabetizes a dict into OrderedDict '''
    return OrderedDict(sorted(dictionary.items(), key=lambda t: t[0]))


def presetToString(dictionary: Dict[str, Any]) -> str:
    '''Returns string repr of a preset'''
    return repr(alphabetizeDict(dictionary))


def presetFromString(string: str) -> Dict[str, Any]:
    '''Turns a string repr of OrderedDict into a regular dict'''
    return dict(eval(string)) # Using eval is generally discouraged, but it is the way that the original code is written


def appendUppercase(lst: List[str]) -> List[str]:
    return lst + [form.upper() for form in lst]


def pipeWrapper(func: Callable[..., subprocess.Popen]) -> Callable[..., subprocess.Popen]:
    '''A decorator to insert proper kwargs into Popen objects.'''
    def pipeWrapper(commandList: List[str], **kwargs: Any) -> subprocess.Popen:
        if sys.platform == 'win32':
            # Stop CMD window from appearing on Windows
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs['startupinfo'] = startupinfo

        if 'bufsize' not in kwargs:
            kwargs['bufsize'] = 10**8
        if 'stdin' not in kwargs:
            kwargs['stdin'] = subprocess.DEVNULL
        return func(commandList, **kwargs)
    return pipeWrapper


@pipeWrapper
def checkOutput(commandList: List[str], **kwargs: Any) -> bytes:
    return subprocess.check_output(commandList, **kwargs)


def disableWhenEncoding(func: Callable) -> Callable:
    def decorator(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.encoding:
            return
        else:
            return func(self, *args, **kwargs)
    return decorator


def disableWhenOpeningProject(func: Callable) -> Callable:
    def decorator(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.core.openingProject:
            return
        else:
            return func(self, *args, **kwargs)
    return decorator


def rgbFromString(string: str) -> Tuple[int, int, int]:
    '''Turns an RGB string like "255, 255, 255" into a tuple'''
    try:
        r, g, b = [int(i) for i in string.split(',')]
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            raise ValueError("RGB values must be between 0 and 255")
        return (r, g, b)
    except ValueError:
        log.warning("Invalid RGB string: '%s'.  Using (255, 255, 255).", string)
        return (255, 255, 255)  # Return white as a default.


# Type variable for widgets, to make connectWidget more type-safe.
W = TypeVar('W', bound=QtWidgets.QWidget)

@overload
def connectWidget(widget: QtWidgets.QLineEdit, func: Callable[[str], None]) -> bool: ...
@overload
def connectWidget(widget: QtWidgets.QSpinBox, func: Callable[[int], None]) -> bool: ...
@overload
def connectWidget(widget: QtWidgets.QDoubleSpinBox, func: Callable[[float], None]) -> bool: ...
@overload
def connectWidget(widget: QtWidgets.QCheckBox, func: Callable[[int], None]) -> bool: ...
@overload
def connectWidget(widget: QtWidgets.QComboBox, func: Callable[[int], None]) -> bool: ...
@overload
def connectWidget(widget: W, func: Callable[..., None]) -> bool: ... # Fallback

def connectWidget(widget: W, func: Callable[..., None]) -> bool:
    if isinstance(widget, QtWidgets.QLineEdit):
        widget.textChanged.connect(func)
    elif isinstance(widget, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
        widget.valueChanged.connect(func)
    elif isinstance(widget, QtWidgets.QCheckBox):
        widget.stateChanged.connect(func)
    elif isinstance(widget, QtWidgets.QComboBox):
        widget.currentIndexChanged.connect(func)
    else:
        log.warning('Failed to connect %s ', str(widget.__class__.__name__))
        return False
    return True


# Type variable for return values of getWidgetValue
WidgetValue = Union[str, int, float, bool]

@overload
def getWidgetValue(widget: QtWidgets.QLineEdit) -> str: ...
@overload
def getWidgetValue(widget: QtWidgets.QSpinBox) -> int: ...
@overload
def getWidgetValue(widget: QtWidgets.QDoubleSpinBox) -> float: ...
@overload
def getWidgetValue(widget: QtWidgets.QCheckBox) -> bool: ...
@overload
def getWidgetValue(widget: QtWidgets.QComboBox) -> int: ...
@overload
def getWidgetValue(widget: W) -> WidgetValue: ...

def getWidgetValue(widget: W) -> WidgetValue:
    '''Generic getValue method for use with any typical QtWidget'''
    if isinstance(widget, QtWidgets.QLineEdit):
        return widget.text()
    elif isinstance(widget, QtWidgets.QSpinBox):
        return widget.value()
    elif isinstance(widget, QtWidgets.QDoubleSpinBox):
        return widget.value()
    elif isinstance(widget, QtWidgets.QCheckBox):
        return widget.isChecked()
    elif isinstance(widget, QtWidgets.QComboBox):
        return widget.currentIndex()
    else:
        log.warning('Failed to get value from %s ', str(widget.__class__.__name__))
        return "" # Added to make the linter happy


@overload
def setWidgetValue(widget: QtWidgets.QLineEdit, val: str) -> bool: ...
@overload
def setWidgetValue(widget: QtWidgets.QSpinBox, val: int) -> bool: ...
@overload
def setWidgetValue(widget: QtWidgets.QDoubleSpinBox, val: float) -> bool: ...
@overload
def setWidgetValue(widget: QtWidgets.QCheckBox, val: bool) -> bool: ...
@overload
def setWidgetValue(widget: QtWidgets.QComboBox, val: int) -> bool: ...
@overload
def setWidgetValue(widget: W, val: WidgetValue) -> bool: ...

def setWidgetValue(widget: W, val: WidgetValue) -> bool:
    '''Generic setValue method for use with any typical QtWidget'''
    log.verbose('Setting %s to %s', str(widget.__class__.__name__), val)
    if isinstance(widget, QtWidgets.QLineEdit):
        widget.setText(str(val))  # Ensure val is a string
    elif isinstance(widget, QtWidgets.QSpinBox):
        widget.setValue(int(val)) # Ensure val is an int
    elif isinstance(widget, QtWidgets.QDoubleSpinBox):
        widget.setValue(float(val))  # Ensure val is a float
    elif isinstance(widget, QtWidgets.QCheckBox):
        widget.setChecked(bool(val))  # Ensure val is a bool
    elif isinstance(widget, QtWidgets.QComboBox):
        widget.setCurrentIndex(int(val))  # Ensure val is an int
    else:
        log.warning('Failed to set %s ', str(widget.__class__.__name__))
        return False
    return True