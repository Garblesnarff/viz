'''
    Preset manager object handles all interactions with presets, including
    the context menu accessed from MainWindow.
'''
from PyQt5 import QtCore, QtWidgets, uic
import string
import os
import logging
from typing import List, Dict, Tuple, Any, Optional

from ..toolkit import badName
from ..core import Core
from .actions import *


log = logging.getLogger('AVP.Gui.PresetManager')


class PresetManager(QtWidgets.QDialog):
    def __init__(self, parent: 'MainWindow') -> None:  # Use forward reference for MainWindow
        super().__init__()
        uic.loadUi(
                os.path.join(Core.wd, 'gui', 'presetmanager.ui'), self)
        self.parent: 'MainWindow' = parent # Type hint
        self.core: Core = parent.core # Type hint
        self.settings: QtCore.QSettings = parent.settings # Type hint
        self.presetDir: str = parent.presetDir # Type hint
        if not self.settings.value('presetDir'):
            self.settings.setValue(
                "presetDir",
                os.path.join(parent.dataDir, 'projects'))

        self.findPresets()

        # window
        self.lastFilter: str = '*'
        self.presetRows: List[Tuple[str, int, str]] = []  # list of (comp, vers, name) tuples
        self.setWindowFlags(QtCore.Qt.WindowStaysOnTopHint)

        # connect button signals
        self.pushButton_delete.clicked.connect(
            self.openDeletePresetDialog
        )
        self.pushButton_rename.clicked.connect(
            self.openRenamePresetDialog
        )
        self.pushButton_import.clicked.connect(
            self.openImportDialog
        )
        self.pushButton_export.clicked.connect(
            self.openExportDialog
        )
        self.pushButton_close.clicked.connect(
            self.close
        )

        # create filter box and preset list
        self.drawFilterList()
        self.comboBox_filter.currentIndexChanged.connect(
            lambda: self.drawPresetList(
                self.comboBox_filter.currentText(),
                self.lineEdit_search.text()
            )
        )

        # make auto-completion for search bar
        self.autocomplete = QtCore.QStringListModel()
        completer = QtWidgets.QCompleter()
        completer.setModel(self.autocomplete)
        self.lineEdit_search.setCompleter(completer)
        self.lineEdit_search.textChanged.connect(
            lambda: self.drawPresetList(
                self.comboBox_filter.currentText(),
                self.lineEdit_search.text()
            )
        )
        self.drawPresetList('*')

    def show_(self) -> None:
        '''Open a new preset manager window from the mainwindow'''
        self.findPresets()
        self.drawFilterList()
        self.drawPresetList('*')
        self.show()

    def findPresets(self) -> None:
        log.debug("Searching %s for presets", self.presetDir)
        parseList: List[Tuple[str, int, str]] = []
        for dirpath, dirnames, filenames in os.walk(self.presetDir):
            # anything without a subdirectory must be a preset folder
            if dirnames:
                continue
            for preset in filenames:
                compName = os.path.basename(os.path.dirname(dirpath))
                if compName not in self.core.compNames:
                    continue
                compVers = os.path.basename(dirpath)
                try:
                    parseList.append((compName, int(compVers), preset))
                except ValueError:
                    continue
        self.presets: Dict[str, List[Tuple[int, str]]] = {
            compName: [
                (vers, preset)
                for name, vers, preset in parseList
                if name == compName
            ]
            for compName, _, __ in parseList
        }

    def drawPresetList(self, compFilter: Optional[str] = None, presetFilter: str = '') -> None:
        self.listWidget_presets.clear()
        if compFilter:
            self.lastFilter = str(compFilter)
        else:
            compFilter = str(self.lastFilter)
        self.presetRows = []
        presetNames = []
        for component, presets in self.presets.items():
            if compFilter != '*' and component != compFilter:
                continue
            for vers, preset in presets:
                if not presetFilter or presetFilter in preset:
                    self.listWidget_presets.addItem(
                        '%s: %s' % (component, preset)
                    )
                    self.presetRows.append((component, vers, preset))
                if preset not in presetNames:
                    presetNames.append(preset)
        self.autocomplete.setStringList(presetNames)

    def drawFilterList(self) -> None:
        self.comboBox_filter.clear()
        self.comboBox_filter.addItem('*')
        for component in self.presets:
            self.comboBox_filter.addItem(component)

    def clearPreset(self, compI: Optional[int] = None) -> None:
        '''Functions on mainwindow level from the context menu'''
        if compI is None:
            compI = self.parent.listWidget_componentList.currentRow()
            if compI == -1:
                return  # No component selected
        action = ClearPreset(self.parent, compI)
        self.parent.undoStack.push(action)

    def openSavePresetDialog(self) -> None:
        '''Functions on mainwindow level from the context menu'''
        selectedComponents = self.core.selectedComponents
        componentList = self.parent.listWidget_componentList

        if componentList.currentRow() == -1:
            return
        while True:
            index = componentList.currentRow()
            currentPreset = selectedComponents[index].currentPreset
            newName, OK = QtWidgets.QInputDialog.getText(
                self.parent,
                'Audio Visualizer',
                'New Preset Name:',
                QtWidgets.QLineEdit.Normal,
                currentPreset if currentPreset else "" #Added default value
            )
            if OK:
                if badName(newName):
                    self.warnMessage(self.parent)
                    continue
                if newName:
                    if index != -1:
                        selectedComponents[index].currentPreset = newName
                        saveValueStore = \
                            selectedComponents[index].savePreset()
                        saveValueStore['preset'] = newName
                        componentName = str(selectedComponents[index]).strip()
                        vers = selectedComponents[index].version
                        self.createNewPreset(
                            componentName, vers, newName,
                            saveValueStore, window=self.parent)
                        self.findPresets()
                        self.drawPresetList()
                        self.openPreset(newName, index)
            break

    def createNewPreset(
            self, compName: str, vers: str, filename: str, saveValueStore: Dict[str, Any], **kwargs: Any) -> None:
        path = os.path.join(self.presetDir, compName, str(vers), filename)
        if self.presetExists(path, **kwargs):
            return
        self.core.createPresetFile(compName, vers, filename, saveValueStore)

    def presetExists(self, path: str, **kwargs: Any) -> bool:
        if os.path.exists(path):
            window = kwargs.get("window", self)
            ch = self.parent.showMessage(
                msg="%s already exists! Overwrite it?" %
                    os.path.basename(path),
                showCancel=True,
                icon='Warning',
                parent=window)
            if not ch:
                # user clicked cancel
                return True

        return False

    def openPreset(self, presetName: str, compPos: Optional[int] = None) -> None:
        componentList = self.parent.listWidget_componentList
        index = compPos if compPos is not None else componentList.currentRow()
        if index == -1:
            return
        action = OpenPreset(self, presetName, index)
        self.parent.undoStack.push(action)

    def _openPreset(self, presetName: str, index: int) -> None:
        selectedComponents = self.core.selectedComponents

        componentName = selectedComponents[index].name.strip()
        version = selectedComponents[index].version
        dirname = os.path.join(self.presetDir, componentName, str(version))
        filepath = os.path.join(dirname, presetName)
        self.core.openPreset(filepath, index, presetName)

        self.parent.updateComponentTitle(index)
        self.parent.drawPreview()

    def openDeletePresetDialog(self) -> None:
        row = self.getPresetRow()
        if row == -1:
            return
        comp, vers, name = self.presetRows[row]
        ch = self.parent.showMessage(
            msg='Really delete %s?' % name,
            showCancel=True,
            icon='Warning',
            parent=self
        )
        if not ch:
            return
        self.deletePreset(comp, vers, name)

    def deletePreset(self, comp: str, vers: str, name: str) -> None:
        action = DeletePreset(self, comp, vers, name)
        self.parent.undoStack.push(action)

    def warnMessage(self, window: Optional[QtWidgets.QWidget] = None) -> None:
        self.parent.showMessage(
            msg='Preset names must contain only letters, '
            'numbers, and spaces.',
            parent=window if window else self)

    def getPresetRow(self) -> int:
        row = self.listWidget_presets.currentRow()
        if row > -1:
            return row

        # check if component selected in MainWindow has preset loaded
        componentList = self.parent.listWidget_componentList
        compIndex = componentList.currentRow()
        if compIndex == -1:
            return compIndex

        preset = self.core.selectedComponents[compIndex].currentPreset
        if preset is None:
            return -1
        else:
            rowTuple = (
                self.core.selectedComponents[compIndex].name,
                self.core.selectedComponents[compIndex].version,
                preset
            )
            for i, tup in enumerate(self.presetRows):
                if rowTuple == tup:
                    index = i
                    break
            else:
                    return -1
        return index

    def openImportDialog(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Preset File",
            self.settings.value("presetDir"),
            "Preset Files (*.avl)")
        if filename:
            # get installed path & ask user to overwrite if needed
            path = ''
            while True:
                if path:
                    if self.presetExists(path):
                        break
                    else:
                        if os.path.exists(path):
                            os.remove(path)
                success, path = self.core.importPreset(filename)
                if success:
                    break

            self.findPresets()
            self.drawPresetList()
            self.settings.setValue("presetDir", os.path.dirname(filename))

    def openExportDialog(self) -> None:
        index = self.getPresetRow()
        if index == -1:
            return
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Preset",
            self.settings.value("presetDir"),
            "Preset Files (*.avl)")
        if filename:
            comp, vers, name = self.presetRows[index]
            if not self.core.exportPreset(filename, comp, vers, name):
                self.parent.showMessage(
                    msg='Couldn\'t export %s.' % filename,
                    parent=self
                )
            self.settings.setValue("presetDir", os.path.dirname(filename))

    def clearPresetListSelection(self) -> None:
        self.listWidget_presets.setCurrentRow(-1)