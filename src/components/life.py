from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QUndoCommand
from PIL import Image, ImageDraw, ImageEnhance, ImageChops, ImageFilter
import os
import math
from typing import List, Set, Tuple, Optional, Dict, Any, Callable

from ..component import Component
from ..toolkit.frame import BlankFrame, scale


class Component(Component):
    name = 'Conway\'s Game of Life'
    version = '1.0.0'

    def widget(self, *args: Any) -> None:
        super().widget(*args)
        self.scale: int = 32
        self.updateGridSize()
        # The initial grid: a "Queen Bee Shuttle"
        # https://conwaylife.com/wiki/Queen_bee_shuttle
        self.startingGrid: Set[Tuple[int, int]] = set([
            (3, 7), (3, 8),
            (4, 7), (4, 8),
            (8, 7),
            (9, 6), (9, 8),
            (10, 5), (10, 9),
            (11, 6), (11, 7), (11, 8),
            (12, 4), (12, 5), (12, 9), (12, 10),
            (23, 6), (23, 7),
            (24, 6), (24, 7)
        ])

        # Amount of 'bleed' (off-canvas coordinates) on each side of the grid
        self.bleedSize: int = 40

        self.page.pushButton_pickImage.clicked.connect(self.pickImage)
        self.trackWidgets({
            'tickRate': self.page.spinBox_tickRate,
            'scale': self.page.spinBox_scale,
            'color': self.page.lineEdit_color,
            'shapeType': self.page.comboBox_shapeType,
            'shadow': self.page.checkBox_shadow,
            'customImg': self.page.checkBox_customImg,
            'showGrid': self.page.checkBox_showGrid,
            'image': self.page.lineEdit_image,
        }, colorWidgets={
            'color': self.page.pushButton_color,
        })
        self.shiftButtons: Tuple[QtWidgets.QToolButton, ...] = (
            self.page.toolButton_up,
            self.page.toolButton_down,
            self.page.toolButton_left,
            self.page.toolButton_right,
        )

        def shiftFunc(i: int) -> Callable[[], None]:  # Correctly captures i
            def shift() -> None:
                self.shiftGrid(i)
            return shift

        shiftFuncs: List[Callable[[], None]] = [shiftFunc(i) for i in range(len(self.shiftButtons))]
        for i, widget in enumerate(self.shiftButtons):
            widget.clicked.connect(shiftFuncs[i])
        self.page.spinBox_scale.setValue(self.scale)
        self.page.spinBox_scale.valueChanged.connect(self.updateGridSize)

    def pickImage(self) -> None:
        imgDir = self.settings.value("componentDir", os.path.expanduser("~"))
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.page, "Choose Image", imgDir,
            "Image Files (%s)" % " ".join(self.core.imageFormats))
        if filename:
            self.settings.setValue("componentDir", os.path.dirname(filename))
            self.mergeUndo = False
            self.page.lineEdit_image.setText(filename)
            self.mergeUndo = True

    def shiftGrid(self, d: int) -> None:
        action = ShiftGrid(self, d)
        self.parent.undoStack.push(action)

    def update(self) -> None:
        self.updateGridSize()
        if self.page.checkBox_customImg.isChecked():
            self.page.label_color.setVisible(False)
            self.page.lineEdit_color.setVisible(False)
            self.page.pushButton_color.setVisible(False)
            self.page.label_shape.setVisible(False)
            self.page.comboBox_shapeType.setVisible(False)
            self.page.label_image.setVisible(True)
            self.page.lineEdit_image.setVisible(True)
            self.page.pushButton_pickImage.setVisible(True)
        else:
            self.page.label_color.setVisible(True)
            self.page.lineEdit_color.setVisible(True)
            self.page.pushButton_color.setVisible(True)
            self.page.label_shape.setVisible(True)
            self.page.comboBox_shapeType.setVisible(True)
            self.page.label_image.setVisible(False)
            self.page.lineEdit_image.setVisible(False)
            self.page.pushButton_pickImage.setVisible(False)
        enabled = (len(self.startingGrid) > 0)
        for widget in self.shiftButtons:
            widget.setEnabled(enabled)

    def previewClickEvent(self, pos: Tuple[int, int], size: Tuple[int, int], button: int) -> None:
        pos = (
            math.ceil((pos[0] / size[0]) * self.gridWidth) - 1,
            math.ceil((pos[1] / size[1]) * self.gridHeight) - 1
        )
        action = ClickGrid(self, pos, button)
        self.parent.undoStack.push(action)

    def updateGridSize(self) -> None:
        w, h = self.core.resolutions[-1].split('x')
        self.gridWidth: int = int(int(w) / self.scale)
        self.gridHeight: int = int(int(h) / self.scale)
        self.pxWidth: int = math.ceil(self.width / self.gridWidth)
        self.pxHeight: int = math.ceil(self.height / self.gridHeight)

    def previewRender(self) -> Image.Image:
        return self.drawGrid(self.startingGrid)

    def preFrameRender(self, *args: Any, **kwargs: Any) -> None:
        super().preFrameRender(*args, **kwargs)
        self.tickGrids: Dict[int, Set[Tuple[int, int]]] = {0: self.startingGrid}

    def properties(self) -> List[str]:
        if self.customImg and (  # type: ignore
                not self.image or not os.path.exists(self.image)):  # type: ignore
            return ['error']
        return []

    def error(self) -> Optional[str]:
        return "No image selected to represent life."

    def frameRender(self, frameNo: int) -> Image.Image:
        tick = math.floor(frameNo / self.tickRate) # type: ignore

        # Compute grid evolution on this frame if it hasn't been computed yet
        if tick not in self.tickGrids:
            self.tickGrids[tick] = self.gridForTick(tick)
        grid = self.tickGrids[tick]

        # Delete old evolution data which we shouldn't need anymore
        if tick - 60 in self.tickGrids:
            del self.tickGrids[tick - 60]
        return self.drawGrid(grid)

    def drawGrid(self, grid: Set[Tuple[int, int]]) -> Image.Image:
        frame = BlankFrame(self.width, self.height)

        def drawCustomImg() -> None:
            try:
                img = Image.open(self.image) # type: ignore
            except Exception:
                return
            img = img.resize((self.pxWidth, self.pxHeight), Image.ANTIALIAS)
            frame.paste(img, box=(drawPtX, drawPtY))

        def drawShape() -> None:
            drawer = ImageDraw.Draw(frame)
            rect = (
                (drawPtX, drawPtY),
                (drawPtX + self.pxWidth, drawPtY + self.pxHeight)
            )
            shape = self.page.comboBox_shapeType.currentText().lower()

            # Rectangle
            if shape == 'rectangle':
                drawer.rectangle(rect, fill=self.color) # type: ignore

            # Elliptical
            elif shape == 'elliptical':
                drawer.ellipse(rect, fill=self.color) # type: ignore

            tenthX, tenthY = scale(10, self.pxWidth, self.pxHeight, int)
            smallerShape = (
                (drawPtX + tenthX + int(tenthX / 4),
                    drawPtY + tenthY + int(tenthY / 2)),
                (drawPtX + self.pxWidth - tenthX - int(tenthX / 4),
                    drawPtY + self.pxHeight - (tenthY + int(tenthY / 2)))
            )
            outlineShape = (
                (drawPtX + int(tenthX / 4),
                    drawPtY + int(tenthY / 2)),
                (drawPtX + self.pxWidth - int(tenthX / 4),
                    drawPtY + self.pxHeight - int(tenthY / 2))
            )
            # Circle
            if shape == 'circle':
                drawer.ellipse(outlineShape, fill=self.color) # type: ignore
                drawer.ellipse(smallerShape, fill=(0, 0, 0, 0))

            # Lilypad
            elif shape == 'lilypad':
                drawer.pieslice(smallerShape, 290, 250, fill=self.color) # type: ignore

            # Pie
            elif shape == 'pie':
                drawer.pieslice(outlineShape, 35, 320, fill=self.color) # type: ignore

            hX, hY = scale(50, self.pxWidth, self.pxHeight, int)  # halfline
            tX, tY = scale(33, self.pxWidth, self.pxHeight, int)  # thirdline
            qX, qY = scale(20, self.pxWidth, self.pxHeight, int)  # quarterline

            # Path
            if shape == 'path':
                drawer.ellipse(rect, fill=self.color) # type: ignore
                rects: Dict[str, bool] = {
                    direction: False
                    for direction in (
                        'up', 'down', 'left', 'right',
                    )
                }
                for cell in self.nearbyCoords(x, y):
                    if cell not in grid:
                        continue
                    if cell[0] == x:
                        if cell[1] < y:
                            rects['up'] = True
                        if cell[1] > y:
                            rects['down'] = True
                    if cell[1] == y:
                        if cell[0] < x:
                            rects['left'] = True
                        if cell[0] > x:
                            rects['right'] = True

                for direction, rect_ in rects.items():
                    if rect_:
                        if direction == 'up':
                            sect = (
                                (drawPtX, drawPtY),
                                (drawPtX + self.pxWidth, drawPtY + hY)
                            )
                        elif direction == 'down':
                            sect = (
                                (drawPtX, drawPtY + hY),
                                (drawPtX + self.pxWidth,
                                    drawPtY + self.pxHeight)
                            )
                        elif direction == 'left':
                            sect = (
                                (drawPtX, drawPtY),
                                (drawPtX + hX,
                                    drawPtY + self.pxHeight)
                            )
                        elif direction == 'right':
                            sect = (
                                (drawPtX + hX, drawPtY),
                                (drawPtX + self.pxWidth,
                                    drawPtY + self.pxHeight)
                            )
                        drawer.rectangle(sect, fill=self.color) # type: ignore

            # Duck
            elif shape == 'duck':
                duckHead = (
                    (drawPtX + qX, drawPtY + qY),
                    (drawPtX + int(qX * 3), drawPtY + int(tY * 2))
                )
                duckBeak = (
                    (drawPtX + hX, drawPtY + qY),
                    (drawPtX + self.pxWidth + qX,
                        drawPtY + int(qY * 3))
                )
                duckWing = (
                    (drawPtX, drawPtY + hY),
                    rect[1]
                )
                duckBody = (
                    (drawPtX + int(qX / 4), drawPtY + int(qY * 3)),
                    (drawPtX + int(tX * 2), drawPtY + self.pxHeight)
                )
                drawer.ellipse(duckBody, fill=self.color) # type: ignore
                drawer.ellipse(duckHead, fill=self.color) # type: ignore
                drawer.pieslice(duckWing, 130, 200, fill=self.color) # type: ignore
                drawer.pieslice(duckBeak, 145, 200, fill=self.color) # type: ignore

            # Peace
            elif shape == 'peace':
                line = ((
                    drawPtX + hX - int(tenthX / 2), drawPtY + int(tenthY / 2)),
                    (drawPtX + hX + int(tenthX / 2),
                        drawPtY + self.pxHeight - int(tenthY / 2))
                )
                drawer.ellipse(outlineShape, fill=self.color) # type: ignore
                drawer.ellipse(smallerShape, fill=(0, 0, 0, 0))
                drawer.rectangle(line, fill=self.color) # type: ignore

                def slantLine(difference: int) -> Tuple[Tuple[int, int], Tuple[int, int]]:
                    return (
                        (drawPtX + difference), (drawPtY + self.pxHeight - qY)
                    ), (
                        (drawPtX + hX), (drawPtY + hY)
                    )

                drawer.line(
                    slantLine(qX),
                    fill=self.color, # type: ignore
                    width=tenthX
                )
                drawer.line(
                    slantLine(self.pxWidth - qX),
                    fill=self.color, # type: ignore
                    width=tenthX
                )

        for x, y in grid:
            drawPtX = x * self.pxWidth
            if drawPtX > self.width:
                continue
            drawPtY = y * self.pxHeight
            if drawPtY > self.height:
                continue

            if self.customImg: # type: ignore
                drawCustomImg()
            else:
                drawShape()

        if self.shadow: # type: ignore
            shadImg = ImageEnhance.Contrast(frame).enhance(0.0)
            shadImg = shadImg.filter(ImageFilter.GaussianBlur(5.00))
            shadImg = ImageChops.offset(shadImg, -2, 2)
            shadImg.paste(frame, box=(0, 0), mask=frame)
            frame = shadImg
        if self.showGrid: # type: ignore
            drawer = ImageDraw.Draw(frame)
            w, h = scale(0.05, self.width, self.height, int)
            for x in range(self.pxWidth, self.width, self.pxWidth):
                drawer.rectangle(
                    ((x, 0),
                        (x + w, self.height)),
                    fill=self.color, # type: ignore
                )
            for y in range(self.pxHeight, self.height, self.pxHeight):
                drawer.rectangle(
                    ((0, y),
                        (self.width, y + h)),
                    fill=self.color, # type: ignore
                )

        return frame

    def gridForTick(self, tick: int) -> Set[Tuple[int, int]]:
        '''
        Given a tick number over 0, returns a new grid (a set of tuples).
        This must compute the previous ticks' grids if not already computed
        '''
        if tick - 1 not in self.tickGrids:
            self.tickGrids[tick - 1] = self.gridForTick(tick - 1)
        
        lastGrid = self.tickGrids[tick - 1]

        def neighbours(x: int, y: int) -> Set[Tuple[int, int]]:
            return {
                cell for cell in self.nearbyCoords(x, y)
                if cell in lastGrid
            }

        newGrid: Set[Tuple[int, int]] = set()
        # Copy cells from the previous grid if they have 2 or 3 neighbouring cells
        # and if they are within the grid or its bleed area (off-canvas area)
        for x, y in lastGrid:
            if (
                    -self.bleedSize > x > self.gridWidth + self.bleedSize
                    or
                    -self.bleedSize > y > self.gridHeight + self.bleedSize
                ):
                continue
            surrounding = len(neighbours(x, y))
            if surrounding == 2 or surrounding == 3:
                newGrid.add((x, y))

        # Find positions around living cells which must be checked for reproduction
        potentialNewCells: Set[Tuple[int, int]] = {
            coordTup for origin in lastGrid
            for coordTup in list(self.nearbyCoords(*origin))
        }
        # Check for reproduction
        for x, y in potentialNewCells:
            if (x, y) in newGrid:
                # Ignore non-empty cell
                continue
            surrounding = len(neighbours(x, y))
            if surrounding == 3:
                newGrid.add((x, y))

        return newGrid

    def savePreset(self) -> Dict[str, Any]:
        pr = super().savePreset()
        pr['GRID'] = sorted(self.startingGrid)
        return pr

    def loadPreset(self, pr: Dict[str, Any], *args: Any) -> None:
        self.startingGrid = set(pr['GRID'])
        if self.startingGrid:
            for widget in self.shiftButtons:
                widget.setEnabled(True)
        super().loadPreset(pr, *args)

    def nearbyCoords(self, x: int, y: int) -> Tuple[Tuple[int, int], ...]:
        yield x + 1, y + 1
        yield x + 1, y - 1
        yield x - 1, y + 1
        yield x - 1, y - 1
        yield x, y + 1
        yield x, y - 1
        yield x + 1, y
        yield x - 1, y


class ClickGrid(QUndoCommand):
    def __init__(self, comp: Component, pos: Tuple[int, int], id_: int) -> None:
        super().__init__(
            "click %s component #%s" % (comp.name, comp.compPos))
        self.comp = comp
        self.pos: List[Tuple[int, int]] = [pos]
        self.id_: int = id_  # Use a different name to avoid conflict with id()

    def id(self) -> int:
        return self.id_

    def mergeWith(self, other: 'ClickGrid') -> bool:
        self.pos.extend(other.pos)
        return True

    def add(self) -> None:
        for pos in self.pos[:]:
            self.comp.startingGrid.add(pos)
        self.comp.update(auto=True)

    def remove(self) -> None:
        for pos in self.pos[:]:
            self.comp.startingGrid.discard(pos)
        self.comp.update(auto=True)

    def redo(self) -> None:
        if self.id_ == 1:  # Left-click
            self.add()
        elif self.id_ == 2:  # Right-click
            self.remove()

    def undo(self) -> None:
        if self.id_ == 1:  # Left-click
            self.remove()
        elif self.id_ == 2:  # Right-click
            self.add()

class ShiftGrid(QUndoCommand):
    def __init__(self, comp: Component, direction: int) -> None:
        super().__init__(
            "change %s component #%s" % (comp.name, comp.compPos))
        self.comp = comp
        self.direction = direction
        self.distance = 1

    def id(self) -> int:
        return self.direction

    def mergeWith(self, other: 'ShiftGrid') -> bool:
        self.distance += other.distance
        return True

    def newGrid(self, Xchange: int, Ychange: int) -> Set[Tuple[int, int]]:
        return {
            (x + Xchange, y + Ychange)
            for x, y in self.comp.startingGrid
        }

    def redo(self) -> None:
        if self.direction == 0:
            newGrid = self.newGrid(0, -self.distance)
        elif self.direction == 1:
            newGrid = self.newGrid(0, self.distance)
        elif self.direction == 2:
            newGrid = self.newGrid(-self.distance, 0)
        elif self.direction == 3:
            newGrid = self.newGrid(self.distance, 0)
        self.comp.startingGrid = newGrid
        self.comp._sendUpdateSignal()

    def undo(self) -> None:
        if self.direction == 0:
            newGrid = self.newGrid(0, self.distance)
        elif self.direction == 1:
            newGrid = self.newGrid(0, -self.distance)
        elif self.direction == 2:
            newGrid = self.newGrid(self.distance, 0)
        elif self.direction == 3:
            newGrid = self.newGrid(-self.distance, 0)
        self.comp.startingGrid = newGrid
        self.comp._sendUpdateSignal()