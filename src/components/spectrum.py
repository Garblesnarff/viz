from PIL import Image
from PyQt5 import QtGui, QtCore, QtWidgets
import os
import math
import subprocess
import time
import logging
from typing import List, Dict, Any, Tuple, Optional

from ..component import Component
from ..toolkit.frame import BlankFrame, scale
from ..toolkit import checkOutput, connectWidget
from ..toolkit.ffmpeg import (
    openPipe, closePipe, getAudioDuration, FfmpegVideo, exampleSound
)


log = logging.getLogger('AVP.Components.Spectrum')


class Component(Component):
    name = 'Spectrum'
    version = '1.0.1'

    def widget(self, *args: Any) -> None:
        self.previewFrame: Optional[QtGui.QImage] = None
        super().widget(*args)
        self._image: Image.Image = BlankFrame(self.width, self.height)
        self.chunkSize: int = 4 * self.width * self.height
        self.changedOptions: bool = True
        self.previewSize: Tuple[int, int] = (214, 120)
        self.previewPipe: Optional[subprocess.Popen] = None

        if hasattr(self.parent, 'lineEdit_audioFile'):
            # update preview when audio file changes (if genericPreview is off)
            self.parent.lineEdit_audioFile.textChanged.connect(
                self.update
            )

        self.trackWidgets({
            'filterType': self.page.comboBox_filterType,
            'window': self.page.comboBox_window,
            'mode': self.page.comboBox_mode,
            'amplitude': self.page.comboBox_amplitude0,
            'amplitude1': self.page.comboBox_amplitude1,
            'amplitude2': self.page.comboBox_amplitude2,
            'display': self.page.comboBox_display,
            'zoom': self.page.spinBox_zoom,
            'tc': self.page.spinBox_tc,
            'x': self.page.spinBox_x,
            'y': self.page.spinBox_y,
            'mirror': self.page.checkBox_mirror,
            'draw': self.page.checkBox_draw,
            'scale': self.page.spinBox_scale,
            'color': self.page.comboBox_color,
            'compress': self.page.checkBox_compress,
            'mono': self.page.checkBox_mono,
            'hue': self.page.spinBox_hue,
        }, relativeWidgets=[
            'x', 'y',
        ])
        for widget in self._trackedWidgets.values():
            connectWidget(widget, lambda: self.changed())

    def changed(self) -> None:
        self.changedOptions = True

    def update(self) -> None:
        filterType = self.page.comboBox_filterType.currentIndex()
        self.page.stackedWidget.setCurrentIndex(filterType)
        if filterType == 3:
            self.page.spinBox_hue.setEnabled(False)
        else:
            self.page.spinBox_hue.setEnabled(True)
        if filterType == 2 or filterType == 4:
            self.page.checkBox_mono.setEnabled(False)
        else:
            self.page.checkBox_mono.setEnabled(True)

    def previewRender(self) -> QtGui.QImage:
        changedSize = self.updateChunksize()
        if not changedSize \
                and not self.changedOptions \
                and self.previewFrame is not None:
            log.debug(
                'Spectrum #%s is reusing old preview frame' % self.compPos)
            return self.previewFrame

        frame = self.getPreviewFrame()
        self.changedOptions = False
        if not frame:
            log.warning(
                'Spectrum #%s failed to create a preview frame' % self.compPos)
            self.previewFrame = None
            return QtGui.QImage() # Return a null QImage
        else:
            self.previewFrame = frame
            return frame

    def preFrameRender(self, **kwargs: Any) -> None:
        super().preFrameRender(**kwargs)
        if self.previewPipe is not None:
            self.previewPipe.wait()
        self.updateChunksize()
        w, h = scale(self.scale, self.width, self.height, str) # type: ignore
        self.video = FfmpegVideo(
            inputPath=self.audioFile, # type: ignore
            filter_=self.makeFfmpegFilter(),
            width=int(w), height=int(h),
            chunkSize=self.chunkSize,
            frameRate=int(self.settings.value("outputFrameRate")),
            parent=self.parent, component=self,
        )

    def frameRender(self, frameNo: int) -> QtGui.QImage:
        if FfmpegVideo.threadError is not None:
            raise FfmpegVideo.threadError
        frame = self.finalizeFrame(self.video.frame(frameNo)) # type: ignore
        return frame # type: ignore

    def postFrameRender(self) -> None:
        closePipe(self.video.pipe) # type: ignore

    def getPreviewFrame(self) -> Optional[QtGui.QImage]:
        genericPreview = self.settings.value("pref_genericPreview")
        startPt = 0.0
        if not genericPreview:
            inputFile = self.parent.lineEdit_audioFile.text()
            if not inputFile or not os.path.exists(inputFile):
                return None
            duration = getAudioDuration(inputFile)
            if not duration:
                return None
            startPt = duration / 3

        command = [
            self.core.FFMPEG_BIN,
            '-thread_queue_size', '512',
            '-r', str(self.settings.value("outputFrameRate")),
            '-ss', "{0:.3f}".format(startPt),
            '-i',
            self.core.junkStream
            if genericPreview else inputFile,
            '-f', 'image2pipe',
            '-pix_fmt', 'rgba',
        ]
        command.extend(self.makeFfmpegFilter(preview=True, startPt=startPt))
        command.extend([
            '-an',
            '-s:v', '%sx%s' % scale(self.scale, self.width, self.height, str), # type: ignore
            '-codec:v', 'rawvideo', '-',
            '-frames:v', '1',
        ])

        if self.core.logEnabled:
            logFilename = os.path.join(
                self.core.logDir, 'preview_%s.log' % str(self.compPos))
            log.debug('Creating FFmpeg process (log at %s)' % logFilename)
            with open(logFilename, 'w') as logf:
                logf.write(" ".join(command) + '\n\n')
            with open(logFilename, 'a') as logf:
                self.previewPipe = openPipe(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=logf, bufsize=10**8
                )
        else:
            self.previewPipe = openPipe(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=10**8
            )
        if not self.previewPipe:
            return None
        byteFrame = self.previewPipe.stdout.read(self.chunkSize) # type: ignore
        closePipe(self.previewPipe)

        frame = self.finalizeFrame(byteFrame)
        return frame

    def makeFfmpegFilter(self, preview: bool = False, startPt: float = 0.0) -> List[str]:
        '''Makes final FFmpeg filter command'''

        def getFilterComplexCommand() -> str:
            '''Inner function that creates the final, complex part of the filter command'''
            nonlocal self
            genericPreview = self.settings.value("pref_genericPreview")

            def getFilterComplexCommandForType() -> str:
                '''Determine portion of filter command that changes depending on selected type'''
                nonlocal self
                if preview:
                    w, h = self.previewSize
                else:
                    w, h = (self.width, self.height)
                color = self.page.comboBox_color.currentText().lower()

                if self.filterType == 0:  # Spectrum
                    if self.amplitude == 0: # type: ignore
                        amplitude = 'sqrt'
                    elif self.amplitude == 1: # type: ignore
                        amplitude = 'cbrt'
                    elif self.amplitude == 2: # type: ignore
                        amplitude = '4thrt'
                    elif self.amplitude == 3: # type: ignore
                        amplitude = '5thrt'
                    elif self.amplitude == 4: # type: ignore
                        amplitude = 'lin'
                    elif self.amplitude == 5: # type: ignore
                        amplitude = 'log'
                    filter_ = (
                        f'showspectrum=s={w}x{h}:'
                        'slide=scroll:'
                        f'win_func={self.page.comboBox_window.currentText()}:'
                        f'color={color}:'
                        f'scale={amplitude},'
                        'colorkey=color=black:'
                        'similarity=0.1:blend=0.5'
                    )
                elif self.filterType == 1:  # Histogram
                    if self.amplitude1 == 0: # type: ignore
                        amplitude = 'log'
                    elif self.amplitude1 == 1: # type: ignore
                        amplitude = 'lin'
                    if self.display == 0: # type: ignore
                        display = 'log'
                    elif self.display == 1: # type: ignore
                        display = 'sqrt'
                    elif self.display == 2: # type: ignore
                        display = 'cbrt'
                    elif self.display == 3: # type: ignore
                        display = 'lin'
                    elif self.display == 4: # type: ignore
                        display = 'rlog'
                    filter_ = (
                        f'ahistogram=r={str(self.settings.value("outputFrameRate"))}:'
                        f's={w}x{h}:'
                        'dmode=separate:'
                        f'ascale={amplitude}:'
                        f'scale={display}'
                    )
                elif self.filterType == 2:  # Vector Scope
                    if self.amplitude2 == 0: # type: ignore
                        amplitude = 'log'
                    elif self.amplitude2 == 1: # type: ignore
                        amplitude = 'sqrt'
                    elif self.amplitude2 == 2: # type: ignore
                        amplitude = 'cbrt'
                    elif self.amplitude2 == 3: # type: ignore
                        amplitude = 'lin'
                    m = self.page.comboBox_mode.currentText()
                    filter_ = (
                        f'avectorscope=s={w}x{h}:'
                        f'draw={"line" if self.draw else "dot"}:' # type: ignore
                        f'm={m}:'
                        f'scale={amplitude}:'
                        f'zoom={str(self.zoom)}' # type: ignore
                    )
                elif self.filterType == 3:  # Musical Scale
                    filter_ = (
                        f'showcqt=r={str(self.settings.value("outputFrameRate"))}:'
                        f's={w}x{h}:'
                        'count=30:'
                        'text=0:'
                        f'tc={str(self.tc)},' # type: ignore
                        'colorkey=color=black:'
                        'similarity=0.1:blend=0.5'
                    )
                elif self.filterType == 4:  # Phase
                    filter_ = (
                        f'aphasemeter=r={str(self.settings.value("outputFrameRate"))}:'
                        f's={w}x{h}:'
                        'video=1 [atrash][vtmp1]; '
                        '[atrash] anullsink; '
                        '[vtmp1] colorkey=color=black:'
                        'similarity=0.1:blend=0.5, '
                        'crop=in_w/8:in_h:(in_w/8)*7:0  '
                    )
                return filter_


            if self.filterType < 2: # type: ignore
                exampleSnd = exampleSound('freq')
            elif self.filterType == 2 or self.filterType == 4: # type: ignore
                exampleSnd = exampleSound('stereo')
            elif self.filterType == 3: # type: ignore
                exampleSnd = exampleSound('white')
            compression = 'compand=gain=4,' if self.compress else '' # type: ignore
            aformat = 'aformat=channel_layouts=mono,' if self.mono and self.filterType not in (2, 4) else '' # type: ignore
            filter_ = getFilterComplexCommandForType()
            hflip = 'hflip, ' if self.mirror else '' # type: ignore
            trim = 'trim=start=%s:end=%s, ' % ("{0:.3f}".format(startPt + 12), "{0:.3f}".format(startPt + 12.5)) if preview else ''
            scale_ = 'scale=%sx%s' % scale(self.scale, self.width, self.height, str) # type: ignore
            hue = ', hue=h=%s:s=10' % str(self.hue) if self.hue > 0 and self.filterType != 3 else '' # type: ignore
            convolution = ', convolution=-2 -1 0 -1 1 1 0 1 2:-2 -1 0 -1 1 1 0 1 2:-2 -1 0 -1 1 1 0 1 2:-2 -1 0 -1 1 1 0 1 2' if self.filterType == 3 else '' # type: ignore

            return (
                f"{exampleSnd if preview and genericPreview else '[0:a] '}"
                f"{compression}{aformat}{filter_} [v1]; "
                f"[v1] {hflip}{trim}{scale_}{hue}{convolution} [v]"
            )


        return [
            '-filter_complex',
            getFilterComplexCommand(),
            '-map', '[v]',
        ]

    def updateChunksize(self) -> bool:
        width, height = scale(self.scale, self.width, self.height, int) # type: ignore
        oldChunkSize = int(self.chunkSize)
        self.chunkSize = 4 * width * height
        changed = self.chunkSize != oldChunkSize
        return changed

    def finalizeFrame(self, imageData: bytes) -> QtGui.QImage:
        try:
            image = Image.frombytes(
                'RGBA',
                scale(self.scale, self.width, self.height, int), # type: ignore
                imageData
            )
            self._image = image
        except ValueError:
            image = self._image

        frame = BlankFrame(self.width, self.height)
        frame.paste(image, box=(self.x, self.y)) # type: ignore
        return frame