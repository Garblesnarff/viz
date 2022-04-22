'''
    When using commandline mode, this module's object handles interpreting
    the arguments and giving them to Core, which tracks the main program state.
    Then it immediately exports a video.
'''
from PyQt5 import QtCore
import argparse
import os
import sys
import time
import signal
import logging

from . import core


log = logging.getLogger('AVP.Commandline')


class Command(QtCore.QObject):
    """
        This replaces the GUI MainWindow when in commandline mode.
    """

    createVideo = QtCore.pyqtSignal()

    def __init__(self):
        QtCore.QObject.__init__(self)
        self.core = core.Core()
        core.Core.mode = 'commandline'
        self.dataDir = self.core.dataDir
        self.canceled = False
        self.settings = core.Core.settings

        # ctrl-c stops the export thread
        signal.signal(signal.SIGINT, self.stopVideo)

    def parseArgs(self):
        self.parser = argparse.ArgumentParser(
            description='Create a visualization for an audio file',
            epilog='EXAMPLE COMMAND:   main.py myvideotemplate.avp '
                        '-i ~/Music/song.mp3 -o ~/video.mp4 '
                        '-c 0 image path=~/Pictures/thisWeeksPicture.jpg '
                        '-c 1 video "preset=My Logo" -c 2 vis layout=classic'
        )
        self.parser.add_argument(
            '-t', '--test', action='store_true',
            help='run tests and generate a logfile to report a bug'
        )
        self.parser.add_argument(
            '-i', '--input', metavar='SOUND',
            help='input audio file'
        )
        self.parser.add_argument(
            '-o', '--output', metavar='OUTPUT',
            help='output video file'
        )
        self.parser.add_argument(
            '-e', '--export', action='store_true',
            help='use input and output files from project file'
        )

        # optional arguments
        self.parser.add_argument(
            'projpath', metavar='path-to-project',
            help='open a project file (.avp)', nargs='?')
        self.parser.add_argument(
            '-c', '--comp', metavar=('LAYER', 'ARG'),
            help='first arg must be component NAME to insert at LAYER.'
            '"help" for information about possible args for a component.',
            nargs='*', action='append')

        self.args = self.parser.parse_args()

        if self.args.test:
            self.runTests()
            quit(0)

        if self.args.projpath:
            projPath = self.args.projpath
            if not os.path.dirname(projPath):
                projPath = os.path.join(
                    self.settings.value("projectDir"),
                    projPath
                )
            if not projPath.endswith('.avp'):
                projPath += '.avp'
            success = self.core.openProject(self, projPath)
            if not success:
                quit(1)
            self.core.selectedComponents = list(
                reversed(self.core.selectedComponents))
            self.core.componentListChanged()

        if self.args.comp:
            for comp in self.args.comp:
                pos = comp[0]
                name = comp[1]
                args = comp[2:]
                try:
                    pos = int(pos)
                except ValueError:
                    print(pos, 'is not a layer number.')
                    quit(1)
                realName = self.parseCompName(name)
                if not realName:
                    print(name, 'is not a valid component name.')
                    quit(1)
                modI = self.core.moduleIndexFor(realName)
                i = self.core.insertComponent(pos, modI, self)
                for arg in args:
                    self.core.selectedComponents[i].command(arg)

        if self.args.export and self.args.projpath:
            errcode, data = self.core.parseAvFile(projPath)
            for key, value in data['WindowFields']:
                if 'outputFile' in key:
                    output = value
                    if not os.path.dirname(value):
                        output = os.path.join(
                            os.path.expanduser('~'),
                            output
                        )
                if 'audioFile' in key:
                    input = value
            self.createAudioVisualisation(input, output)

        elif self.args.input and self.args.output:
            self.createAudioVisualisation(self.args.input, self.args.output)

        elif 'help' not in sys.argv:
            self.parser.print_help()
            quit(1)

    def createAudioVisualisation(self, input, output):
        self.core.selectedComponents = list(
            reversed(self.core.selectedComponents))
        self.core.componentListChanged()
        self.worker = self.core.newVideoWorker(
            self, input, output
        )
        self.worker.videoCreated.connect(self.videoCreated)
        self.lastProgressUpdate = time.time()
        self.worker.progressBarSetText.connect(self.progressBarSetText)
        self.createVideo.emit()

    def stopVideo(self, *args):
        self.worker.error = True
        self.worker.cancelExport()
        self.worker.cancel()

    @QtCore.pyqtSlot(str)
    def progressBarSetText(self, value):
        if 'Export ' in value:
            # Don't duplicate completion/failure messages
            return
        if not value.startswith('Exporting') \
                and time.time() - self.lastProgressUpdate >= 0.05:
            # Show most messages very often
            print(value)
        elif time.time() - self.lastProgressUpdate >= 2.0:
            # Give user time to read ffmpeg's output during the export
            print('##### %s' % value)
        else:
            return
        self.lastProgressUpdate = time.time()

    @QtCore.pyqtSlot()
    def videoCreated(self):
        quit(0)

    def showMessage(self, **kwargs):
        print(kwargs['msg'])
        if 'detail' in kwargs:
            print(kwargs['detail'])

    @QtCore.pyqtSlot(str, str)
    def videoThreadError(self, msg, detail):
        print(msg)
        print(detail)
        quit(1)

    def drawPreview(self, *args):
        pass

    def parseCompName(self, name):
        '''Deduces a proper component name out of a commandline arg'''

        if name.title() in self.core.compNames:
            return name.title()
        for compName in self.core.compNames:
            if name.capitalize() in compName:
                return compName

        compFileNames = [
            os.path.splitext(
                os.path.basename(mod.__file__)
            )[0]
            for mod in self.core.modules
        ]
        for i, compFileName in enumerate(compFileNames):
            if name.lower() in compFileName:
                return self.core.compNames[i]
            return

        return None

    def runTests(self):
        core.FILE_LOGLVL = logging.DEBUG
        from . import tests
        test_report = os.path.join(core.Core.logDir, "test_report.log")
        tests.run(test_report)
        with open(test_report, "r") as f:
            output = f.readlines()
        print("".join(output))
