from PIL import Image
from PyQt5 import QtWidgets
from PyQt5.QtGui import QColor
import os
import logging
from typing import List, Dict, Any, Tuple, Optional

from ..component import Component
from ..toolkit.frame import BlankFrame, scale
from ..toolkit import checkOutput
from ..toolkit.ffmpeg import (
    openPipe, closePipe, getAudioDuration, FfmpegVideo, exampleSound
)


log = logging.getLogger('AVP.Components.Waveform')


class Component(Component):
    name = 'Waveform'
    version = '1.0.0'

    def widget(self, *args: Any) -> None:
        super().widget(*args)
        self._image: Image.Image = BlankFrame(self.width, self.height)

        self.page.lineEdit_color.setText('255,255,255')

        if hasattr(self.parent, 'lineEdit_audioFile'):
            self.parent.lineEdit_audioFile.textChanged.connect(
                self.update
            )

        self.trackWidgets({
            'color': self.page.lineEdit_color,
            'mode': self.page.comboBox_mode,
            'amplitude': self.page.comboBox_amplitude,
            'x': self.page.spinBox_x,
            'y': self.page.spinBox_y,
            'mirror': self.page.checkBox_mirror,
            'scale': self.page.spinBox_scale,
            'opacity': self.page.spinBox_opacity,
            'compress': self.page.checkBox_compress,
            'mono': self.page.checkBox_mono,
        }, colorWidgets={
            'color': self.page.pushButton_color,
        }, relativeWidgets=[
            'x', 'y',
        ])

    def previewRender(self) -> QtGui.QImage:
        self.updateChunksize()
        frame = self.getPreviewFrame(self.width, self.height)
        if not frame:
            return QtGui.QImage()  # Return a null QImage if frame is None
        else:
            return frame

    def preFrameRender(self, **kwargs: Any) -> None:
        super().preFrameRender(**kwargs)
        self.updateChunksize()
        w, h = scale(self.scale, self.width, self.height, str) # type: ignore
        self.video = FfmpegVideo(
            inputPath=self.audioFile, # type: ignore
            filter_=self.makeFfmpegFilter(),
            width=int(w), height=int(h), # Ensure int
            chunkSize=self.chunkSize,
            frameRate=int(self.settings.value("outputFrameRate")),
            parent=self.parent, component=self, debug=True,
        )

    def frameRender(self, frameNo: int) -> QtGui.QImage:
        if FfmpegVideo.threadError is not None:
            raise FfmpegVideo.threadError
        return self.finalizeFrame(self.video.frame(frameNo)) # type: ignore

    def postFrameRender(self) -> None:
        closePipe(self.video.pipe) # type: ignore

    def getPreviewFrame(self, width: int, height: int) -> Optional[QtGui.QImage]:
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
            if startPt + 3 > duration:
                startPt += startPt - 3

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
            log.debug('Creating ffmpeg log at %s', logFilename)
            with open(logFilename, 'w') as logf:
                logf.write(" ".join(command) + '\n\n')
            with open(logFilename, 'a') as logf:
                pipe = openPipe(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=logf, bufsize=10**8
                )
        else:
            pipe = openPipe(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=10**8
            )
        if not pipe:
            return None

        byteFrame = pipe.stdout.read(self.chunkSize) # type: ignore
        closePipe(pipe)

        frame = self.finalizeFrame(byteFrame)
        return frame

    def makeFfmpegFilter(self, preview: bool = False, startPt: float = 0.0) -> List[str]:
        w, h = scale(self.scale, self.width, self.height, str) # type: ignore
        if self.amplitude == 0: # type: ignore
            amplitude = 'lin'
        elif self.amplitude == 1: # type: ignore
            amplitude = 'log'
        elif self.amplitude == 2: # type: ignore
            amplitude = 'sqrt'
        elif self.amplitude == 3: # type: ignore
            amplitude = 'cbrt'
        hexcolor = QColor(*self.color).name() # type: ignore
        opacity = "{0:.1f}".format(self.opacity / 100) # type: ignore
        genericPreview = self.settings.value("pref_genericPreview")
        if self.mode < 3: # type: ignore
            filter_ = (
                'showwaves='
                f'r={str(self.settings.value("outputFrameRate"))}:'
                f's={self.settings.value("outputWidth")}x{self.settings.value("outputHeight")}:'
                f'mode={self.page.comboBox_mode.currentText().lower() if self.mode != 3 else "p2p"}:' # type: ignore
                f'colors={hexcolor}@{opacity}:scale={amplitude}'
            )
        elif self.mode > 2: # type: ignore
            filter_ = (
                f'showfreqs=s={str(self.settings.value("outputWidth"))}x{str(self.settings.value("outputHeight"))}:'
                f'mode={"line" if self.mode == 4 else "bar"}:' # type: ignore
                f'colors={hexcolor}@{opacity}'
                f":ascale={amplitude}:fscale={'log' if self.mono else 'lin'}" # type: ignore
            )

        baselineHeight = int(self.height * (4 / 1080))
        return [
            '-filter_complex',
            f"{exampleSound('wave', extra='') if preview and genericPreview else '[0:a] '}"
            f"{'compand=gain=4,' if self.compress else ''}" # type: ignore
            f"{'aformat=channel_layouts=mono,' if self.mono and self.mode < 3 else ''}" # type: ignore
            f"{filter_}"
            f"{', drawbox=x=(iw-w)/2:y=(ih-h)/2:w=iw:h=%s:color=%s@%s' % (baselineHeight, hexcolor, opacity) if self.mode < 2 else ''}" # type: ignore
            f"{', hflip' if self.mirror else''}" # type: ignore
            " [v1]; "
            '[v1] scale=%s:%s%s [v]' % (
                w, h,
                ', trim=duration=%s' % "{0:.3f}".format(startPt + 3)
                if preview else '',
            ),
            '-map', '[v]',
        ]

    def updateChunksize(self) -> None:
        width, height = scale(self.scale, self.width, self.height, int) # type: ignore
        self.chunkSize = 4 * width * height

    def finalizeFrame(self, imageData: bytes) -> QtGui.QImage:
        try:
            image = Image.frombytes(
                'RGBA',
                scale(self.scale, self.width, self.height, int), # type: ignore
                imageData
            )
            self._image = image
        except ValueError:
            # use last good frame
            image = self._image

        if self.scale != 100 \
                or self.x != 0 or self.y != 0: # type: ignore
            frame = BlankFrame(self.width, self.height)
            frame.paste(image, box=(self.x, self.y)) # type: ignore
        else:
            frame = image
        return frame