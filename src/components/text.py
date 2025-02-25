from PIL import ImageEnhance, ImageFilter, ImageChops
from PyQt5.QtGui import QColor, QFont
from PyQt5 import QtGui, QtCore, QtWidgets
import os
import logging
from typing import List, Dict, Any, Tuple, Optional, Union

from ..component import Component
from ..toolkit.frame import FramePainter, PaintColor

log = logging.getLogger('AVP.Components.Text')


class Component(Component):
    name = 'Title Text'
    version = '1.0.1'

    def widget(self, *args: Any) -> None:
        super().widget(*args)
        self.title: str = 'Text'
        self.alignment: int = 1
        self.titleFont: QtGui.QFont = QFont()
        self.fontSize: float = self.height / 13.5

        self.page.comboBox_textAlign.addItem("Left")
        self.page.comboBox_textAlign.addItem("Middle")
        self.page.comboBox_textAlign.addItem("Right")
        self.page.comboBox_textAlign.setCurrentIndex(int(self.alignment))
        self.page.spinBox_fontSize.setValue(int(self.fontSize))
        self.page.lineEdit_title.setText(self.title)
        self.page.pushButton_center.clicked.connect(self.centerXY)

        self.page.fontComboBox_titleFont.currentFontChanged.connect(self._sendUpdateSignal)
        # The QFontComboBox must be connected directly to the Qt Signal
        # which triggers the preview to update.
        # This unfortunately makes changing the font into a non-undoable action.
        # Must be something broken in the conversion to a ComponentAction

        self.trackWidgets({
            'textColor': self.page.lineEdit_textColor,
            'title': self.page.lineEdit_title,
            'alignment': self.page.comboBox_textAlign,
            'fontSize': self.page.spinBox_fontSize,
            'xPosition': self.page.spinBox_xTextAlign,
            'yPosition': self.page.spinBox_yTextAlign,
            'fontStyle': self.page.comboBox_fontStyle,
            'stroke': self.page.spinBox_stroke,
            'strokeColor': self.page.lineEdit_strokeColor,
            'shadow': self.page.checkBox_shadow,
            'shadX': self.page.spinBox_shadX,
            'shadY': self.page.spinBox_shadY,
            'shadBlur': self.page.spinBox_shadBlur,
        }, colorWidgets={
            'textColor': self.page.pushButton_textColor,
            'strokeColor': self.page.pushButton_strokeColor,
        }, relativeWidgets=[
            'xPosition', 'yPosition', 'fontSize',
            'stroke', 'shadX', 'shadY', 'shadBlur'
        ])
        self.centerXY()

    def update(self) -> None:
        self.titleFont = self.page.fontComboBox_titleFont.currentFont()
        if self.page.checkBox_shadow.isChecked():
            self.page.label_shadX.setHidden(False)
            self.page.spinBox_shadX.setHidden(False)
            self.page.spinBox_shadY.setHidden(False)
            self.page.label_shadBlur.setHidden(False)
            self.page.spinBox_shadBlur.setHidden(False)
        else:
            self.page.label_shadX.setHidden(True)
            self.page.spinBox_shadX.setHidden(True)
            self.page.spinBox_shadY.setHidden(True)
            self.page.label_shadBlur.setHidden(True)
            self.page.spinBox_shadBlur.setHidden(True)

    def centerXY(self) -> None:
        self.setRelativeWidget('xPosition', 0.5)
        self.setRelativeWidget('yPosition', 0.521)

    def getXY(self) -> Tuple[int, int]:
        '''Returns true x, y after considering alignment settings'''
        fm = QtGui.QFontMetrics(self.titleFont)
        x = self.pixelValForAttr('xPosition')

        if self.alignment == 1:             # Middle
            offset = int(fm.width(self.title)/2)
            x -= offset
        if self.alignment == 2:             # Right
            offset = fm.width(self.title) # type: ignore
            x -= offset

        return x, self.yPosition # type: ignore

    def loadPreset(self, pr: Dict[str, Any], *args: Any) -> None:
        super().loadPreset(pr, *args)

        font = QFont()
        font.fromString(pr['titleFont'])
        self.page.fontComboBox_titleFont.setCurrentFont(font)

    def savePreset(self) -> Dict[str, Any]:
        saveValueStore = super().savePreset()
        saveValueStore['titleFont'] = self.titleFont.toString()
        return saveValueStore

    def previewRender(self) -> QtGui.QImage:
        return self.addText(self.width, self.height)

    def properties(self) -> List[str]:
        props = ['static']
        if not self.title: # type: ignore
            props.append('error')
        return props

    def error(self) -> Optional[str]:
        return "No text provided."

    def frameRender(self, frameNo: int) -> QtGui.QImage:
        return self.addText(self.width, self.height)

    def addText(self, width: int, height: int) -> QtGui.QImage:
        font = self.titleFont
        font.setPixelSize(int(self.fontSize)) #Ensures that the value is an integer
        font.setStyle(QFont.StyleNormal)
        font.setWeight(QFont.Normal)
        font.setCapitalization(QFont.MixedCase)
        if self.fontStyle == 1: # type: ignore
            font.setWeight(QFont.DemiBold)
        if self.fontStyle == 2: # type: ignore
            font.setWeight(QFont.Bold)
        elif self.fontStyle == 3: # type: ignore
            font.setStyle(QFont.StyleItalic)
        elif self.fontStyle == 4: # type: ignore
            font.setWeight(QFont.Bold)
            font.setStyle(QFont.StyleItalic)
        elif self.fontStyle == 5: # type: ignore
            font.setStyle(QFont.StyleOblique)
        elif self.fontStyle == 6: # type: ignore
            font.setCapitalization(QFont.SmallCaps)

        image = FramePainter(width, height)
        x, y = self.getXY()
        log.debug('Text position translates to %s, %s', x, y)
        if self.stroke > 0: # type: ignore
            outliner = QtGui.QPainterPathStroker()
            outliner.setWidth(self.stroke) # type: ignore
            path = QtGui.QPainterPath()
            if self.fontStyle == 6: # type: ignore
                # PathStroker ignores smallcaps so we need this weird hack
                path.addText(x, y, font, self.title[0]) # type: ignore
                fm = QtGui.QFontMetrics(font)
                newX = x + fm.width(self.title[0]) # type: ignore
                strokeFont = self.page.fontComboBox_titleFont.currentFont()
                strokeFont.setCapitalization(QFont.SmallCaps)
                strokeFont.setPixelSize(int((self.fontSize / 7) * 5)) # type: ignore
                strokeFont.setLetterSpacing(QFont.PercentageSpacing, 139) # type: ignore
                path.addText(newX, y, strokeFont, self.title[1:]) # type: ignore
            else:
                path.addText(x, y, font, self.title) # type: ignore
            path = outliner.createStroke(path)
            image.setPen(QtCore.Qt.NoPen) # type: ignore
            image.setBrush(PaintColor(*self.strokeColor)) # type: ignore
            image.drawPath(path)

        image.setFont(font)
        image.setPen(self.textColor) # type: ignore
        image.drawText(x, y, self.title) # type: ignore

        # turn QImage into Pillow frame
        frame = image.finalize() # type: ignore
        if self.shadow: # type: ignore
            shadImg = ImageEnhance.Contrast(frame).enhance(0.0)
            shadImg = shadImg.filter(ImageFilter.GaussianBlur(self.shadBlur)) # type: ignore
            shadImg = ImageChops.offset(shadImg, self.shadX, self.shadY) # type: ignore
            shadImg.paste(frame, box=(0, 0), mask=frame)
            frame = shadImg

        return frame

    def commandHelp(self) -> None:
        print('Enter a string to use as centred white text:')
        print('    "title=User Error"')
        print('Specify a text color:\n    color=255,255,255')
        print('Set custom x, y position:\n    x=500 y=500')

    def command(self, arg: str) -> None:
        if '=' in arg:
            key, arg = arg.split('=', 1)
            if key == 'color':
                self.page.lineEdit_textColor.setText(arg)
                return
            elif key == 'size':
                self.page.spinBox_fontSize.setValue(int(arg))
                return
            elif key == 'x':
                self.page.spinBox_xTextAlign.setValue(int(arg))
                return
            elif key == 'y':
                self.page.spinBox_yTextAlign.setValue(int(arg))
                return
            elif key == 'title':
                self.page.lineEdit_title.setText(arg)
                return
        super().command(arg)