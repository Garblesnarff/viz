from PIL import Image, ImageDraw, ImageEnhance
from PyQt5 import QtGui, QtCore, QtWidgets
import os
from typing import List, Dict, Any, Tuple, Optional

from ..component import Component
from ..toolkit.frame import BlankFrame

class Component(Component):
    name = 'Image'
    version = '1.0.1'

    def widget(self, *args: Any) -> None:
        super().widget(*args)
        self.page.pushButton_image.clicked.connect(self.pickImage)
        self.trackWidgets({
            'imagePath': self.page.lineEdit_image,
            'scale': self.page.spinBox_scale,
            'stretchScale': self.page.spinBox_scale_stretch,
            'rotate': self.page.spinBox_rotate,
            'color': self.page.spinBox_color,
            'xPosition': self.page.spinBox_x,
            'yPosition': self.page.spinBox_y,
            'stretched': self.page.checkBox_stretch,
            'mirror': self.page.checkBox_mirror,
        }, presetNames={
            'imagePath': 'image',
            'xPosition': 'x',
            'yPosition': 'y',
        }, relativeWidgets=[
            'xPosition', 'yPosition', 'scale'
        ])

    def previewRender(self) -> QtGui.QImage:
        return self.drawFrame(self.width, self.height)

    def properties(self) -> List[str]:
        props = ['static']
        if not os.path.exists(self.imagePath): # type: ignore
            props.append('error')
        return props

    def error(self) -> Optional[str]:
        if not self.imagePath: # type: ignore
            return "There is no image selected."
        if not os.path.exists(self.imagePath): # type: ignore
            return "The image selected does not exist!"
        return None

    def frameRender(self, frameNo: int) -> QtGui.QImage:
        return self.drawFrame(self.width, self.height)

    def drawFrame(self, width: int, height: int) -> QtGui.QImage:
        frame = BlankFrame(width, height)
        if self.imagePath and os.path.exists(self.imagePath): # type: ignore
            scale = self.scale if not self.stretched else self.stretchScale # type: ignore
            image = Image.open(self.imagePath) # type: ignore

            # Modify image's appearance
            if self.color != 100: # type: ignore
                image = ImageEnhance.Color(image).enhance(
                    float(self.color / 100) # type: ignore
                )
            if self.mirror: # type: ignore
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            if self.stretched and image.size != (width, height): # type: ignore
                image = image.resize((width, height), Image.ANTIALIAS)
            if scale != 100:
                newHeight = int((image.height / 100) * scale)
                newWidth = int((image.width / 100) * scale)
                image = image.resize((newWidth, newHeight), Image.ANTIALIAS)

            # Paste image at correct position
            frame.paste(image, box=(self.xPosition, self.yPosition)) # type: ignore
            if self.rotate != 0: # type: ignore
                frame = frame.rotate(self.rotate) # type: ignore

        return frame

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

    def command(self, arg: str) -> None:
        if '=' in arg:
            key, arg = arg.split('=', 1)
            if key == 'path' and os.path.exists(arg):
                try:
                    Image.open(arg)
                    self.page.lineEdit_image.setText(arg)
                    self.page.checkBox_stretch.setChecked(True)
                    return
                except OSError as e:
                    print("Not a supported image format")
                    quit(1)
        super().command(arg)

    def commandHelp(self) -> None:
        print('Load an image:\n    path=/filepath/to/image.png')

    def savePreset(self) -> Dict[str, Any]:
        # Maintain the illusion that the scale spinbox is one widget
        scaleBox = self.page.spinBox_scale
        stretchScaleBox = self.page.spinBox_scale_stretch
        if self.page.checkBox_stretch.isChecked():
            scaleBox.setValue(stretchScaleBox.value())
        else:
            stretchScaleBox.setValue(scaleBox.value())
        return super().savePreset()

    def update(self) -> None:
        # Maintain the illusion that the scale spinbox is one widget
        scaleBox = self.page.spinBox_scale
        stretchScaleBox = self.page.spinBox_scale_stretch
        if self.page.checkBox_stretch.isChecked():
            scaleBox.setVisible(False)
            stretchScaleBox.setVisible(True)
        else:
            scaleBox.setVisible(True)
            stretchScaleBox.setVisible(False)