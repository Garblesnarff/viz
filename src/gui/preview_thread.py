'''
    Thread that runs to create QImages for MainWindow's preview label.
    Processes a queue of component lists.
'''
from PyQt5 import QtCore, QtGui, uic
from PyQt5.QtCore import pyqtSignal, pyqtSlot
from PIL import Image
from PIL.ImageQt import ImageQt
from queue import Queue, Empty
import os
import logging
from typing import List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..component import Component
    from ..core import Core

from ..toolkit.frame import Checkerboard
from ..toolkit import disableWhenOpeningProject

log = logging.getLogger("AVP.Gui.PreviewThread")


class Worker(QtCore.QObject):

    imageCreated = pyqtSignal(QtGui.QImage)
    error = pyqtSignal(str)

    def __init__(self, core: 'Core', settings: QtCore.QSettings, queue: Queue) -> None: # Added Core type
        super().__init__()
        self.core: 'Core' = core  # Added type hint
        self.settings: QtCore.QSettings = settings  # Added type hint
        width = int(self.settings.value('outputWidth'))
        height = int(self.settings.value('outputHeight'))
        self.queue: Queue[List['Component']] = queue # Added more specific type hint.
        self.background: Image.Image = Checkerboard(width, height) # Type hint

    @disableWhenOpeningProject
    @pyqtSlot(list)
    def createPreviewImage(self, components: List['Component']) -> None: # Added type hint
        dic = {
          "components": components,
        }
        self.queue.put(dic)
        log.debug('Preview thread id: {}'.format(int(QtCore.QThread.currentThreadId())))

    @pyqtSlot()
    def process(self) -> None:
        try:
            nextPreviewInformation = self.queue.get(block=False)
            while self.queue.qsize() >= 2:
                try:
                    self.queue.get(block=False)
                except Empty:
                    continue
            width = int(self.settings.value('outputWidth'))
            height = int(self.settings.value('outputHeight'))
            if self.background.width != width \
                    or self.background.height != height:
                self.background = Checkerboard(width, height)

            frame: Image.Image = self.background.copy()
            log.info('Creating new preview frame')
            components: List['Component'] = nextPreviewInformation["components"]
            for component in reversed(components):
                try:
                    component.lockSize(width, height)
                    newFrame: Image.Image = component.previewRender()
                    component.unlockSize()
                    frame = Image.alpha_composite(
                        frame, newFrame
                    )

                except ValueError as e:
                    errMsg = "Bad frame returned by %s's preview renderer. " \
                        "%s. New frame size was %s*%s; should be %s*%s." % (
                            str(component), str(e).capitalize(),
                            newFrame.width, newFrame.height, #type: ignore
                            width, height
                        )
                    log.critical(errMsg)
                    self.error.emit(errMsg)
                    break
                except RuntimeError as e:
                    log.error(str(e))
            else:
                # We must store a reference to this QImage
                # or else Qt will garbage-collect it on the C++ side
                self.frame: ImageQt = ImageQt(frame)  # Store as ImageQt, type hint
                self.imageCreated.emit(QtGui.QImage(self.frame))

        except Empty:
            pass