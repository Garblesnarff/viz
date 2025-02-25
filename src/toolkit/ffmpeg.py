'''
    Tools for using ffmpeg
'''
import numpy as np
import sys
import os
import subprocess as sp
import threading
import signal
from queue import PriorityQueue
import logging
from typing import List, Tuple, Optional, Dict, Any, Sequence, Union

from .. import core
from .common import checkOutput, pipeWrapper

log = logging.getLogger('AVP.Toolkit.Ffmpeg')


class FfmpegVideo:
    '''Opens a pipe to ffmpeg and stores a buffer of raw video frames.'''

    # error from the thread used to fill the buffer
    threadError: Optional[Exception] = None

    def __init__(self, *, inputPath: str,  # Keyword-only arguments for clarity
                width: int, height: int, frameRate: int, chunkSize: int,
                parent: Any, component: Any,  # Replace 'Any' with more specific types if possible
                filter_: Optional[List[str]] = None,
                loopVideo: bool = False,
                debug: bool = False) -> None: #Added debug flag

        mandatoryArgs = [
            'inputPath',
            'filter_',
            'width',
            'height',
            'frameRate',  # frames per second
            'chunkSize',  # number of bytes in one frame
            'parent',     # mainwindow object
            'component',  # component object
        ]
        for arg in mandatoryArgs:
            setattr(self, arg, locals()[arg]) #Dynamically set the attributes from the arguments

        self.frameNo: int = -1
        self.currentFrame: bytes = b'' # Use bytes for raw frame data
        self.map_: Any = None # You might want to define a more specific type if you know what self.map is
        self.pipe: Optional[sp.Popen] = None #type: ignore

        self.loopValue: str = '-1' if loopVideo else '0'

        if filter_ is None:
            filter_ = []
        if filter_[0] != '-filter_complex':
                filter_.insert(0, '-filter_complex')


        self.command: List[str] = [
            core.Core.FFMPEG_BIN,
            '-thread_queue_size', '512',
            '-r', str(self.frameRate),
            '-stream_loop', self.loopValue,
            '-i', self.inputPath,
            '-f', 'image2pipe',
            '-pix_fmt', 'rgba',
        ]
        self.command.extend(filter_)
        self.command.extend([
            '-codec:v', 'rawvideo', '-',
        ])

        self.frameBuffer: PriorityQueue[Tuple[int, bytes]] = PriorityQueue(maxsize=self.frameRate)  # Use PriorityQueue[Tuple[int, bytes]]
        self.finishedFrames: Dict[int, bytes] = {}
        self.lastFrame: bytes = b''

        self.thread = threading.Thread(
            target=self.fillBuffer,
            name='FFmpeg Frame-Fetcher'
        )
        self.thread.daemon = True
        self.thread.start()

    def frame(self, num: int) -> bytes:
        while True:
            if num in self.finishedFrames:
                image = self.finishedFrames.pop(num)
                return image

            i, image = self.frameBuffer.get()
            self.finishedFrames[i] = image
            self.frameBuffer.task_done()

    def fillBuffer(self) -> None:
        from ..component import ComponentError
        if core.Core.logEnabled:
            logFilename = os.path.join(
                core.Core.logDir, 'render_%s.log' % str(self.component.compPos)
            )
            log.debug('Creating ffmpeg process (log at %s)', logFilename)
            with open(logFilename, 'w') as logf:
                logf.write(" ".join(self.command) + '\n\n')
            with open(logFilename, 'a') as logf:
                self.pipe = openPipe(
                    self.command, stdin=sp.DEVNULL,
                    stdout=sp.PIPE, stderr=logf, bufsize=10**8
                )
        else:
            self.pipe = openPipe(
                self.command, stdin=sp.DEVNULL, stdout=sp.PIPE,
                stderr=sp.DEVNULL, bufsize=10**8
            )

        if not self.pipe:
            raise RuntimeError("FFmpeg pipe could not be opened.")

        while True:
            if self.parent.canceled:
                break
            self.frameNo += 1

            # If we run out of frames, use the last good frame and loop.
            try:
                if len(self.currentFrame) == 0:
                    self.frameBuffer.put((self.frameNo-1, self.lastFrame))
                    continue
            except AttributeError:
                FfmpegVideo.threadError = ComponentError(
                    self.component, 'video',
                    "Video seemed playable but wasn't."
                )
                break

            try:
                self.currentFrame = self.pipe.stdout.read(self.chunkSize)  # type: ignore
            except ValueError as e:
                if str(e) == "PyMemoryView_FromBuffer(): info->buf must not be NULL":
                    log.debug("Ignored 'info->buf must not be NULL' error from FFmpeg pipe")
                    return
                else:
                    FfmpegVideo.threadError = ComponentError(
                        self.component, 'video')

            if len(self.currentFrame) != 0:
                self.frameBuffer.put((self.frameNo, self.currentFrame))
                self.lastFrame = self.currentFrame


@pipeWrapper
def openPipe(commandList: List[str], **kwargs: Any) -> sp.Popen:
    return sp.Popen(commandList, **kwargs)


def closePipe(pipe: Optional[sp.Popen]) -> None:  # Allow pipe to be None
    if pipe: # Check that pipe is not None
        if pipe.stdout:
            pipe.stdout.close()
        pipe.send_signal(signal.SIGTERM)
        pipe.wait()


def findFfmpeg() -> str:
    if sys.platform == "win32":
        bin = 'ffmpeg.exe'
    else:
        bin = 'ffmpeg'

    if getattr(sys, 'frozen', False):
        # The application is frozen
        bin = os.path.join(core.Core.wd, bin)

    with open(os.devnull, "w") as f:
        try:
            checkOutput(
                [bin, '-version'], stderr=f
            )
        except (sp.CalledProcessError, FileNotFoundError):
            bin = ""

    return bin


def createFfmpegCommand(inputFile: str, outputFile: str, components: List[Any], duration: float = -1.0) -> List[str]:
    '''
        Constructs the major ffmpeg command used to export the video
    '''
    if duration == -1.0:
        duration_float = getAudioDuration(inputFile)
        if duration_float is None:
            log.error("Audio duration could not be determined.")
            return []
        duration = duration_float


    safeDuration = "{0:.3f}".format(duration - 0.05)  # used by filters
    duration_str = "{0:.3f}".format(duration + 0.1)  # used by input sources
    Core = core.Core

    # Test if user has libfdk_aac
    try:
        encoders_bytes = checkOutput(
            f"{Core.FFMPEG_BIN} -encoders -hide_banner", shell=True
        )
        encoders = encoders_bytes.decode("utf-8")
    except (sp.CalledProcessError, FileNotFoundError) as e:
        log.error(f"Error checking FFmpeg encoders: {e}")
        return []


    acodec = Core.settings.value('outputAudioCodec')

    options = Core.encoderOptions
    containerName = Core.settings.value('outputContainer')
    vcodec = Core.settings.value('outputVideoCodec')
    vbitrate = str(Core.settings.value('outputVideoBitrate'))+'k'
    acodec = Core.settings.value('outputAudioCodec')
    abitrate = str(Core.settings.value('outputAudioBitrate'))+'k'

    for cont in options['containers']:
        if cont['name'] == containerName:
            container = cont['container']
            break
    else:  # No matching container found
        log.error(f"Invalid container name: {containerName}")
        return []

    vencoders = options['video-codecs'].get(vcodec)
    aencoders = options['audio-codecs'].get(acodec)

    if vencoders is None:
        log.error(f"No video encoders found for codec: {vcodec}")
        return []
    if aencoders is None:
        log.error(f"No audio encoders found for codec: {acodec}")
        return []


    def error() -> List[str]:
        nonlocal encoders, encoder
        log.critical("Selected encoder (%s) is not supported by Ffmpeg. The supported encoders are: %s", encoder, encoders)
        return []

    for encoder in vencoders:
        if encoder in encoders:
            vencoder = encoder
            break
    else:
        return error()

    for encoder in aencoders:
        if encoder in encoders:
            aencoder = encoder
            break
    else:
        return error()

    ffmpegCommand = [
        Core.FFMPEG_BIN,
        '-thread_queue_size', '512',
        '-y',  # overwrite the output file if it already exists.

        # INPUT VIDEO
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', f'{Core.settings.value("outputWidth")}x{Core.settings.value("outputHeight")}',
        '-pix_fmt', 'rgba',
        '-r', str(Core.settings.value('outputFrameRate')),
        '-t', duration_str,
        '-an',  # the video input has no sound
        '-i', '-',  # the video input comes from a pipe

        # INPUT SOUND
        '-t', duration_str,
        '-i', inputFile
    ]

    extraAudio = [
        comp.audio for comp in components
        if 'audio' in comp.properties()
    ]
    segment = createAudioFilterCommand(extraAudio, safeDuration)
    ffmpegCommand.extend(segment)
    # Map audio from the filters or the single audio input, and map video from the pipe
    ffmpegCommand.extend([
        '-map', '0:v',
        '-map', '[a]' if segment else '1:a',
    ])

    ffmpegCommand.extend([
        # OUTPUT
        '-vcodec', vencoder,
        '-acodec', aencoder,
        '-b:v', vbitrate,
        '-b:a', abitrate,
        '-pix_fmt', Core.settings.value('outputVideoFormat'),
        '-preset', Core.settings.value('outputPreset'),
        '-f', container
    ])

    if acodec == 'AAC':
        ffmpegCommand.append('-strict')
        ffmpegCommand.append('-2')

    ffmpegCommand.append(outputFile)
    return ffmpegCommand


def createAudioFilterCommand(extraAudio: List[Any], duration: str) -> List[str]:
    '''Add extra inputs and any needed filters to the main ffmpeg command.'''
    # NOTE: Global filters are currently hard-coded here for debugging use
    globalFilters = 0  # increase to add global filters

    if not extraAudio and not globalFilters:
        return []

    ffmpegCommand: List[str] = []
    # Add -i options for extra input files
    extraFilters: Dict[int, List[Tuple[str, str]]] = {}
    for streamNo, params in enumerate(reversed(extraAudio)):
        extraInputFile, params = params
        ffmpegCommand.extend([
            '-t', duration,
            # Tell ffmpeg about shorter clips (seemingly not needed)
            #   streamDuration = getAudioDuration(extraInputFile)
            #   if streamDuration and streamDuration > float(safeDuration)
            #   else "{0:.3f}".format(streamDuration),
            '-i', extraInputFile
        ])
        # Construct dataset of extra filters we'll need to add later
        for ffmpegFilter in params:
            if streamNo + 2 not in extraFilters:
                extraFilters[streamNo + 2] = []
            extraFilters[streamNo + 2].append((
                ffmpegFilter, params[ffmpegFilter]
            ))

    # Start creating avfilters! Popen-style, so don't use semicolons;
    extraFilterCommand: List[str] = []

    if globalFilters <= 0:
        # Dictionary of last-used tmp labels for a given stream number
        tmpInputs: Dict[int, int] = {streamNo: -1 for streamNo in extraFilters}
    else:
        # Insert blank entries for global filters into extraFilters
        # so the per-stream filters know what input to source later
        for streamNo in range(len(extraAudio), 0, -1):
            if streamNo + 1 not in extraFilters:
                extraFilters[streamNo + 1] = []
        # Also filter the primary audio track
        extraFilters[1] = []
        tmpInputs = {
            streamNo: globalFilters - 1
            for streamNo in extraFilters
        }

        # Add the global filters!
        # NOTE: list length must = globalFilters, currently hardcoded
        if tmpInputs:
            extraFilterCommand.extend([
                '[%s:a] ashowinfo [%stmp0]' % (
                    str(streamNo),
                    str(streamNo)
                )
                for streamNo in tmpInputs
            ])

    # Now add the per-stream filters!
    for streamNo, paramList in extraFilters.items():
        for param in paramList:
            source = '[%s:a]' % str(streamNo) \
                if tmpInputs[streamNo] == -1 else \
                '[%stmp%s]' % (
                    str(streamNo), str(tmpInputs[streamNo])
                )
            tmpInputs[streamNo] = tmpInputs[streamNo] + 1
            extraFilterCommand.append(
                '%s %s%s [%stmp%s]' % (
                    source, param[0], param[1], str(streamNo),
                    str(tmpInputs[streamNo])
                )
            )

    # Join all the filters together and combine into 1 stream
    extraFilterCommand_str = "; ".join(extraFilterCommand) + '; ' \
        if tmpInputs else ''
    ffmpegCommand.extend([
        '-filter_complex',
        extraFilterCommand_str +
        '%s amix=inputs=%s:duration=first [a]'
        % (
            "".join([
                '[%stmp%s]' % (str(i), tmpInputs[i])
                if i in extraFilters else '[%s:a]' % str(i)
                for i in range(1, len(extraAudio) + 2)
            ]),
            str(len(extraAudio) + 1)
        ),
    ])
    return ffmpegCommand


def testAudioStream(filename: str) -> bool:
    '''Test if an audio stream definitely exists'''
    audioTestCommand = [
        core.Core.FFMPEG_BIN,
        '-i', filename,
        '-vn', '-f', 'null', '-'
    ]
    try:
        checkOutput(audioTestCommand, stderr=sp.DEVNULL)
    except sp.CalledProcessError:
        return False
    else:
        return True


def getAudioDuration(filename: str) -> Optional[float]:
    '''Try to get duration of audio file as float, or False if not possible'''
    command = [core.Core.FFMPEG_BIN, '-i', filename]

    try:
        fileInfo_bytes = checkOutput(command, stderr=sp.STDOUT)
        fileInfo = fileInfo_bytes.decode("utf-8")
    except (sp.CalledProcessError, FileNotFoundError, UnicodeDecodeError) as e:
        log.error(f"Error getting audio duration: {e}")
        return None

    for line in fileInfo.split('\n'):
        if 'Duration' in line:
            d = line.split(',')[0].split(' ')[3].split(':')
            try:
                duration = float(d[0])*3600 + float(d[1])*60 + float(d[2])
                return duration
            except (ValueError, IndexError):  # Handle parsing errors
                log.warning("Could not parse duration from FFmpeg output: %s", line)
                return None

    return None  # String not found in output


def readAudioFile(filename: str, videoWorker: Any) -> Optional[Tuple[np.ndarray, float]]:
    '''
        Creates the completeAudioArray given to components
        and used to draw the classic visualizer.
    '''
    duration = getAudioDuration(filename)
    if duration is None:  # Use Optional[float] for duration
        log.error(f"Audio file {filename} doesn't exist or unreadable.")
        return None

    command = [
        core.Core.FFMPEG_BIN,
        '-i', filename,
        '-f', 's16le',
        '-acodec', 'pcm_s16le',
        '-ar', '44100',  # ouput will have 44100 Hz
        '-ac', '1',  # mono (set to '2' for stereo)
        '-']
    pipe = openPipe(
        command,
        stdout=sp.PIPE, stderr=sp.DEVNULL, bufsize=10**8
    )
    if not pipe:
        return None

    completeAudioArray = numpy.empty(0, dtype="int16")

    progress = 0
    lastPercent = None
    while True:
        if core.Core.canceled:
            return None
        # read 2 seconds of audio
        progress += 4
        raw_audio = pipe.stdout.read(88200*4)  # type: ignore
        if len(raw_audio) == 0:
            break
        audio_array = numpy.frombuffer(raw_audio, dtype="int16")  # Use frombuffer
        completeAudioArray = numpy.append(completeAudioArray, audio_array)

        percent = int(100*(progress/duration))
        if percent >= 100:
            percent = 100

        if lastPercent != percent:
            string = 'Loading audio file: '+str(percent)+'%'
            videoWorker.progressBarSetText.emit(string)
            videoWorker.progressBarUpdate.emit(percent)

        lastPercent = percent

    pipe.kill()
    pipe.wait()

    # add 0s the end
    completeAudioArrayCopy = numpy.zeros(
        len(completeAudioArray) + 44100, dtype="int16")
    completeAudioArrayCopy[:len(completeAudioArray)] = completeAudioArray
    completeAudioArray = completeAudioArrayCopy

    return (completeAudioArray, duration)


def exampleSound(
    style: str = 'white', extra: str = 'apulsator=offset_l=0.35:offset_r=0.67') -> str:
    '''Help generate an example sound for use in creating a preview'''

    if style == 'white':
        src = '-2+random(0)'
    elif style == 'freq':
        src = 'sin(1000*t*PI*t)'
    elif style == 'wave':
        src = 'sin(random(0)*2*PI*t)*tan(random(0)*2*PI*t)'
    elif style == 'stereo':
        src = '0.1*sin(2*PI*(360-2.5/2)*t) | 0.1*sin(2*PI*(360+2.5/2)*t)'
    else:
        src = '-2+random(0)'

    return f"aevalsrc='{src}', {extra}{', ' if extra else ''}"