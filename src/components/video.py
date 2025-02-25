from PIL import Image
from PyQt5 import QtGui, QtCore, QtWidgets
import os
import subprocess
import logging
from typing import List, Dict, Any, Tuple, Optional

from ..component import Component
from ..toolkit.frame import BlankFrame, scale
from ..toolkit.ffmpeg import openPipe, closePipe, testAudioStream, FfmpegVideo
from ..toolkit import checkOutput


log = logging.getLogger('AVP.Components.Video')


class Component(Component):
    name = 'Video'
    version = '1.0.0'

    def widget(self, *args: Any) -> None:
        self.videoPath: str = ''
        self.badAudio: bool = False
        self.x: int = 0
        self.y: int = 0
        self.loopVideo: bool = False
        super().widget(*args)
        self._image: Image.Image = BlankFrame(self.width, self.height)
        self.page.pushButton_video.clicked.connect(self.pickVideo)
        self.trackWidgets({
            'videoPath': self.page.lineEdit_video,
            'loopVideo': self.page.checkBox_loop,
            'useAudio': self.page.checkBox_useAudio,
            'distort': self.page.checkBox_distort,
            'scale': self.page.spinBox_scale,
            'volume': self.page.spinBox_volume,
            'xPosition': self.page.spinBox_x,
            'yPosition': self.page.spinBox_y,
        }, presetNames={
            'videoPath': 'video',
            'loopVideo': 'loop',
            'xPosition': 'x',
            'yPosition': 'y',
        }, relativeWidgets=[
            'xPosition', 'yPosition',
        ])

    def update(self) -> None:
        if self.page.checkBox_useAudio.isChecked():
            self.page.label_volume.setEnabled(True)
            self.page.spinBox_volume.setEnabled(True)
        else:
            self.page.label_volume.setEnabled(False)
            self.page.spinBox_volume.setEnabled(False)

    def previewRender(self) -> QtGui.QImage:
        self.updateChunksize()
        frame = self.getPreviewFrame(self.width, self.height)
        if not frame:
            return QtGui.QImage() # Return a null QImage
        else:
            return frame

    def properties(self) -> List[str]:
        props = []
        outputFile = None
        if hasattr(self.parent, 'lineEdit_outputFile'):
            # check only happens in GUI mode
            outputFile = self.parent.lineEdit_outputFile.text()

        if not self.videoPath:  # type: ignore
            self.lockError("There is no video selected.")
        elif not os.path.exists(self.videoPath):  # type: ignore
            self.lockError("The video selected does not exist!")
        elif outputFile and os.path.realpath(self.videoPath) == os.path.realpath(outputFile):  # type: ignore
            self.lockError("Input and output paths match.")

        if self.useAudio: # type: ignore
            props.append('audio')
            if not testAudioStream(self.videoPath) \
                    and self.error() is None:
                self.lockError(
                    "Could not identify an audio stream in this video.")

        return props

    def audio(self) -> Optional[Tuple[str, Dict[str, str]]]:
        params: Dict[str, str] = {}
        if self.volume != 1.0: # type: ignore
            params['volume'] = '=%s:replaygain_noclip=0' % str(self.volume) # type: ignore
        return (self.videoPath, params) # type: ignore

    def preFrameRender(self, **kwargs: Any) -> None:
        super().preFrameRender(**kwargs)
        self.updateChunksize()
        self.video = FfmpegVideo(
            inputPath=self.videoPath, filter_=self.makeFfmpegFilter(), # type: ignore
            width=self.width, height=self.height, chunkSize=self.chunkSize,
            frameRate=int(self.settings.value("outputFrameRate")),
            parent=self.parent, loopVideo=self.loopVideo, # type: ignore
            component=self
        ) if os.path.exists(self.videoPath) else None # type: ignore

    def frameRender(self, frameNo: int) -> QtGui.QImage:
        if FfmpegVideo.threadError is not None:
            raise FfmpegVideo.threadError
        frame = self.finalizeFrame(self.video.frame(frameNo)) # type: ignore
        return frame # type: ignore

    def postFrameRender(self) -> None:
        closePipe(self.video.pipe) # type: ignore

    def pickVideo(self) -> None:
        imgDir = self.settings.value("componentDir", os.path.expanduser("~"))
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.page, "Choose Video",
            imgDir, "Video Files (%s)" % " ".join(self.core.videoFormats)
        )
        if filename:
            self.settings.setValue("componentDir", os.path.dirname(filename))
            self.mergeUndo = False
            self.page.lineEdit_video.setText(filename)
            self.mergeUndo = True

    def getPreviewFrame(self, width: int, height: int) -> Optional[QtGui.QImage]:
        if not self.videoPath or not os.path.exists(self.videoPath): # type: ignore
            return None

        command = [
            self.core.FFMPEG_BIN,
            '-thread_queue_size', '512',
            '-i', self.videoPath, # type: ignore
            '-f', 'image2pipe',
            '-pix_fmt', 'rgba',
        ]
        command.extend(self.makeFfmpegFilter())
        command.extend([
            '-codec:v', 'rawvideo', '-',
            '-ss', '90',
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

    def makeFfmpegFilter(self) -> List[str]:
        return [
            '-filter_complex',
            '[0:v] scale=%s:%s' % scale(
                self.scale, self.width, self.height, str), # type: ignore
        ]

    def updateChunksize(self) -> None:
        if self.scale != 100 and not self.distort: # type: ignore
            width, height = scale(self.scale, self.width, self.height, int) # type: ignore
        else:
            width, height = self.width, self.height
        self.chunkSize = 4 * width * height

    def command(self, arg: str) -> None:
        if '=' in arg:
            key, arg = arg.split('=', 1)
            if key == 'path' and os.path.exists(arg):
                if '*%s' % os.path.splitext(arg)[1] in self.core.videoFormats:
                    self.page.lineEdit_video.setText(arg)
                    self.page.spinBox_scale.setValue(100)
                    self.page.checkBox_loop.setChecked(True)
                    return
                else:
                    print("Not a supported video format")
                    quit(1)
        elif arg == 'audio':
            if not self.page.lineEdit_video.text():
                print("'audio' option must follow a video selection")
                quit(1)
            self.page.checkBox_useAudio.setChecked(True)
            return
        super().command(arg)

    def commandHelp(self) -> None:
        print('Load a video:\n    path=/filepath/to/video.mp4')
        print('Using audio:\n    path=/filepath/to/video.mp4 audio')

    def finalizeFrame(self, imageData: bytes) -> QtGui.QImage:
        try:
            if self.distort: # type: ignore
                image = Image.frombytes(
                    'RGBA',
                    (self.width, self.height),
                    imageData
                )
            else:
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
                or self.xPosition != 0 or self.yPosition != 0: # type: ignore
            frame = BlankFrame(self.width, self.height)
            frame.paste(image, box=(self.xPosition, self.yPosition)) # type: ignore
        else:
            frame = image
        return frame