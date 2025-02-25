import numpy as np
from PIL import Image, ImageDraw
from PyQt5 import QtGui, QtCore, QtWidgets
from PyQt5.QtGui import QColor
import os
import time
from copy import copy
from typing import List, Dict, Any, Tuple, Optional

from ..component import Component
from ..toolkit.frame import BlankFrame


class Component(Component):
    name = 'Classic Visualizer'
    version = '1.0.0'

    def names(*args: Any) -> List[str]:  # type: ignore
        return ['Original Audio Visualization']

    def properties(self) -> List[str]:
        return ['pcm']

    def widget(self, *args: Any) -> None:
        self.scale: int = 20
        self.y: int = 0
        super().widget(*args)

        self.page.comboBox_visLayout.addItem("Classic")
        self.page.comboBox_visLayout.addItem("Split")
        self.page.comboBox_visLayout.addItem("Bottom")
        self.page.comboBox_visLayout.addItem("Top")
        self.page.comboBox_visLayout.setCurrentIndex(0)

        self.page.lineEdit_visColor.setText('255,255,255')

        self.trackWidgets({
            'visColor': self.page.lineEdit_visColor,
            'layout': self.page.comboBox_visLayout,
            'scale': self.page.spinBox_scale,
            'y': self.page.spinBox_y,
            'smooth': self.page.spinBox_smooth,
        }, colorWidgets={
            'visColor': self.pushButton_visColor,
        }, relativeWidgets=[
            'y',
        ])

    def previewRender(self) -> Image.Image:
        spectrum = np.fromfunction(
            lambda x: float(self.scale)/2500*(x-128)**2, (255,), dtype="int16") # type: ignore
        return self.drawBars(
            self.width, self.height, spectrum, self.visColor, self.layout # type: ignore
        )

    def preFrameRender(self, **kwargs: Any) -> None:
        super().preFrameRender(**kwargs)
        self.smoothConstantDown: float = 0.08 + 0 if not self.smooth else self.smooth / 15 # type: ignore
        self.smoothConstantUp: float = 0.8 - 0 if not self.smooth else self.smooth / 15 # type: ignore
        self.lastSpectrum: Optional[np.ndarray] = None
        self.spectrumArray: Dict[int, np.ndarray] = {}

        for i in range(0, len(self.completeAudioArray), self.sampleSize): # type: ignore
            if self.canceled:
                break
            self.lastSpectrum = self.transformData(
                i, self.completeAudioArray, self.sampleSize, # type: ignore
                self.smoothConstantDown, self.smoothConstantUp,
                self.lastSpectrum)
            self.spectrumArray[i] = copy(self.lastSpectrum)

            progress = int(100*(i/len(self.completeAudioArray))) # type: ignore
            if progress >= 100:
                progress = 100
            pStr = "Analyzing audio: "+str(progress)+'%'
            self.progressBarSetText.emit(pStr) # type: ignore
            self.progressBarUpdate.emit(int(progress)) # type: ignore

    def frameRender(self, frameNo: int) -> Image.Image:
        arrayNo = frameNo * self.sampleSize
        return self.drawBars(
            self.width, self.height,
            self.spectrumArray[arrayNo],
            self.visColor, self.layout) # type: ignore

    def transformData(
      self, i: int, completeAudioArray: np.ndarray, sampleSize: int,
      smoothConstantDown: float, smoothConstantUp: float, lastSpectrum: Optional[np.ndarray]
      ) -> np.ndarray:
        if len(completeAudioArray) < (i + sampleSize):
            sampleSize = len(completeAudioArray) - i

        window = np.hanning(sampleSize)
        data = completeAudioArray[i:i+sampleSize][::1] * window
        paddedSampleSize = 2048
        paddedData = np.pad(
            data, (0, paddedSampleSize - sampleSize), 'constant')
        spectrum = np.fft.fft(paddedData)
        sample_rate = 44100
        frequencies = np.fft.fftfreq(len(spectrum), 1./sample_rate)

        y = abs(spectrum[0:int(paddedSampleSize/2) - 1])

        # filter the noise away
        # y[y<80] = 0

        y = self.scale * np.log10(y) # type: ignore
        y[np.isinf(y)] = 0

        if lastSpectrum is not None:
            lastSpectrum[y < lastSpectrum] = \
                y[y < lastSpectrum] * smoothConstantDown + \
                lastSpectrum[y < lastSpectrum] * (1 - smoothConstantDown)

            lastSpectrum[y >= lastSpectrum] = \
                y[y >= lastSpectrum] * smoothConstantUp + \
                lastSpectrum[y >= lastSpectrum] * (1 - smoothConstantUp)
        else:
            lastSpectrum = y

        x = frequencies[0:int(paddedSampleSize/2) - 1]

        return lastSpectrum # type: ignore

    def drawBars(self, width: int, height: int, spectrum: np.ndarray, color: Tuple[int, int, int], layout: int) -> Image.Image:
        vH = height-height/8
        bF = width / 64
        bH = bF / 2
        bQ = bF / 4
        imTop = BlankFrame(width, height)
        draw = ImageDraw.Draw(imTop)
        r, g, b = color
        color2 = (r, g, b, 125)

        bP = height / 1200

        for j in range(0, 63):
            draw.rectangle((
                bH + j * bF, vH+bQ, bH + j * bF + bF, vH + bQ -
                spectrum[j * 4] * bP - bH), fill=color2)

            draw.rectangle((
                bH + bQ + j * bF, vH, bH + bQ + j * bF + bH, vH -
                spectrum[j * 4] * bP), fill=color)

        imBottom = imTop.transpose(Image.FLIP_TOP_BOTTOM)

        im = BlankFrame(width, height)

        if layout == 0:  # Classic
            y = self.y - int(height/100*43) # type: ignore
            im.paste(imTop, (0, y), mask=imTop)
            y = self.y + int(height/100*43) # type: ignore
            im.paste(imBottom, (0, y), mask=imBottom)

        if layout == 1:  # Split
            y = self.y + int(height/100*10) # type: ignore
            im.paste(imTop, (0, y), mask=imTop)
            y = self.y - int(height/100*10) # type: ignore
            im.paste(imBottom, (0, y), mask=imBottom)

        if layout == 2:  # Bottom
            y = self.y + int(height/100*10) # type: ignore
            im.paste(imTop, (0, y), mask=imTop)

        if layout == 3:  # Top
            y = self.y - int(height/100*10) # type: ignore
            im.paste(imBottom, (0, y), mask=imBottom)

        return im

    def command(self, arg: str) -> None:
        if '=' in arg:
            key, arg = arg.split('=', 1)
            try:
                if key == 'color':
                    self.page.lineEdit_visColor.setText(arg)
                    return
                elif key == 'layout':
                    if arg == 'classic':
                        self.page.comboBox_visLayout.setCurrentIndex(0)
                    elif arg == 'split':
                        self.page.comboBox_visLayout.setCurrentIndex(1)
                    elif arg == 'bottom':
                        self.page.comboBox_visLayout.setCurrentIndex(2)
                    elif arg == 'top':
                        self.page.comboBox_visLayout.setCurrentIndex(3)
                    return
                elif key == 'scale':
                    arg = int(arg)
                    self.page.spinBox_scale.setValue(arg)
                    return
                elif key == 'y':
                    arg = int(arg)
                    self.page.spinBox_y.setValue(arg)
                    return
            except ValueError:
                print('You must enter a number.')
                quit(1)
        super().command(arg)

    def commandHelp(self) -> None:
        print('Give a layout name:\n    layout=[classic/split/bottom/top]')
        print('Specify a color:\n    color=255,255,255')
        print('Visualizer scale (20 is default):\n    scale=number')
        print('Y position:\n    y=number')