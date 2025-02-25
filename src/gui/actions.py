'''
    QCommand classes for every undoable user action performed in the MainWindow
'''
from PyQt5.QtWidgets import QUndoCommand
import os
from copy import copy
from typing import List

from ..core import Core

# =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
# COMPONENT ACTIONS
# =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~

class AddComponent(QUndoCommand):
    def __init__(self, parent: 'MainWindow', compI: int, moduleI: int) -> None: # Added type hints, and forward reference for MainWindow
        super().__init__(
            f"create new {parent.core.modules[moduleI].Component.name} component"
        )
        self.parent = parent
        self.moduleI = moduleI
        self.compI = compI
        self.comp = None

    def redo(self) -> None:
        if self.comp is None:
            self.compI = self.parent.core.insertComponent(
                self.compI, self.moduleI, self.parent) #Fixed: must capture the return, as it may change
        else:
            # inserting previously-created component
           self.compI = self.parent.core.insertComponent( #Fixed: must capture the return, as it may change
                self.compI, self.comp, self.parent)


    def undo(self) -> None:
        self.comp = self.parent.core.selectedComponents[self.compI]
        self.parent._removeComponent(self.compI)


class RemoveComponent(QUndoCommand):
    def __init__(self, parent: 'MainWindow', selectedRows: List[QtWidgets.QListWidgetItem]) -> None: # Added type hints
        super().__init__('remove component')
        self.parent = parent
        componentList = self.parent.listWidget_componentList
        self.selectedRows: List[int] = [
            componentList.row(selected) for selected in selectedRows
        ]
        self.components: List['Component'] = [ # Added type hints
            parent.core.selectedComponents[i] for i in self.selectedRows
        ]

    def redo(self) -> None:
        # Sort in reverse order to avoid index issues when removing multiple items
        for index in sorted(self.selectedRows, reverse=True):
            self.parent._removeComponent(index)


    def undo(self) -> None:
        componentList = self.parent.listWidget_componentList
        for index, comp in zip(self.selectedRows, self.components):
            self.parent.core.insertComponent(
                index, comp, self.parent
            )
        self.parent.drawPreview()


class MoveComponent(QUndoCommand):
    def __init__(self, parent: 'MainWindow', startI: int, endI: int) -> None:  # Simplified parameters
        super().__init__(f"move component from {startI} to {endI}")
        self.parent = parent
        self.startI = startI
        self.endI = endI

    def redo(self) -> None:
        self.parent.core.moveComponent(self.startI, self.endI)
        self.parent.update_component_display(self.endI) #Added: method to update the display
        self.parent.drawPreview(True)

    def undo(self) -> None:
        # Move back, but the indices might have changed.  Core handles the actual move.
        self.parent.core.moveComponent(self.endI, self.startI)
        self.parent.update_component_display(self.startI) #Added: method to update the display
        self.parent.drawPreview(True)



# =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~
# PRESET ACTIONS
# =~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~=~==~=~=~=~=~=~=~=~=~=~=~=~=~=~

class ClearPreset(QUndoCommand):
    def __init__(self, parent: 'MainWindow', compI: int) -> None: # Added type hints
        super().__init__("clear preset")
        self.parent = parent
        self.compI = compI
        self.component = self.parent.core.selectedComponents[compI]
        self.store = self.component.savePreset()
        self.store['preset'] = self.component.currentPreset

    def redo(self) -> None:
        self.parent.core.clearPreset(self.compI)
        self.parent.updateComponentTitle(self.compI, False)

    def undo(self) -> None:
        self.parent.core.selectedComponents[self.compI].loadPreset(self.store)
        self.parent.updateComponentTitle(self.compI, self.store)


class OpenPreset(QUndoCommand):
    def __init__(self, parent: 'MainWindow', presetName: str, compI: int) -> None: # Added type hints
        super().__init__(f"open {presetName} preset")
        self.parent = parent
        self.presetName = presetName
        self.compI = compI

        comp = self.parent.core.selectedComponents[compI]
        self.store = comp.savePreset()
        self.store['preset'] = copy(comp.currentPreset)

    def redo(self) -> None:
        self.parent._openPreset(self.presetName, self.compI)

    def undo(self) -> None:
        self.parent.core.selectedComponents[self.compI].loadPreset(
            self.store)
        self.parent.parent.updateComponentTitle(self.compI, self.store)


class RenamePreset(QUndoCommand):
    def __init__(self, parent: 'MainWindow', path: str, oldName: str, newName: str) -> None: # Added type hints
        super().__init__('rename preset')
        self.parent = parent
        self.path = path
        self.oldName = oldName
        self.newName = newName

    def redo(self) -> None:
        self.parent.renamePreset(self.path, self.oldName, self.newName)

    def undo(self) -> None:
        self.parent.renamePreset(self.path, self.newName, self.oldName)


class DeletePreset(QUndoCommand):
    def __init__(self, parent: 'MainWindow', compName: str, vers: str, presetFile: str) -> None: # Added type hints
        self.parent = parent
        self.preset = (compName, vers, presetFile)
        self.path = os.path.join(
            Core.presetDir, compName, str(vers), presetFile
        )
        self.store = self.parent.core.getPreset(self.path)
        self.presetName = self.store['preset']
        super().__init__(f'delete {self.presetName} preset ({compName})')
        self.loadedPresets: List[int] = [
            i for i, comp in enumerate(self.parent.core.selectedComponents)
            if self.presetName == str(comp.currentPreset)
        ]

    def redo(self) -> None:
        os.remove(self.path)
        for i in self.loadedPresets:
            self.parent.core.clearPreset(i)
            self.parent.parent.updateComponentTitle(i, False)
        self.parent.findPresets()
        self.parent.drawPresetList()

    def undo(self) -> None:
        self.parent.createNewPreset(*self.preset, self.store)
        selectedComponents = self.parent.core.selectedComponents
        for i in self.loadedPresets:
            selectedComponents[i].currentPreset = self.presetName
            self.parent.parent.updateComponentTitle(i)
        self.parent.findPresets()
        self.parent.drawPresetList()