from PyQt5 import QtWidgets
import os
from typing import List, Dict, Any, Tuple, Optional

from ..component import Component
from ..toolkit.frame import BlankFrame


class Component(Component):
    name = 'Sound'
    version = '1.0.0'

    def widget(self, *args: Any) -> None:
        super().widget(*args)
        self.page.pushButton_sound.clicked.connect(self.pickSound)
        self.trackWidgets({
            'sound': self.page.lineEdit_sound,
            'chorus': self.page.checkBox_chorus,
            'delay': self.page.spinBox_delay,
            'volume': self.page.spinBox_volume,
        }, commandArgs={
            'sound': None,
        })

    def properties(self) -> List[str]:
        props = ['static', 'audio']
        if not os.path.exists(self.sound): # type: ignore
            props.append('error')
        return props

    def error(self) -> Optional[str]:
        if not self.sound: # type: ignore
            return "No audio file selected."
        if not os.path.exists(self.sound): # type: ignore
            return "The audio file selected no longer exists!"
        return None

    def audio(self) -> Optional[Tuple[str, Dict[str, str]]]:
        params: Dict[str, str] = {}
        if self.delay != 0.0: # type: ignore
            params['adelay'] = '=%s' % str(int(self.delay * 1000.00)) # type: ignore
        if self.chorus: # type: ignore
            params['chorus'] = \
                '=0.5:0.9:50|60|40:0.4|0.32|0.3:0.25|0.4|0.3:2|2.3|1.3'
        if self.volume != 1.0: # type: ignore
            params['volume'] = '=%s:replaygain_noclip=0' % str(self.volume) # type: ignore

        return (self.sound, params) # type: ignore

    def pickSound(self) -> None:
        sndDir = self.settings.value("componentDir", os.path.expanduser("~"))
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.page, "Choose Sound", sndDir,
            "Audio Files (%s)" % " ".join(self.core.audioFormats))
        if filename:
            self.settings.setValue("componentDir", os.path.dirname(filename))
            self.mergeUndo = False
            self.page.lineEdit_sound.setText(filename)
            self.mergeUndo = True

    def commandHelp(self) -> None:
        print('Path to audio file:\n    path=/filepath/to/sound.ogg')

    def command(self, arg: str) -> None:
        if '=' in arg:
            key, arg = arg.split('=', 1)
            if key == 'path':
                if '*%s' % os.path.splitext(arg)[1] \
                        not in self.core.audioFormats:
                    print("Not a supported audio format")
                    quit(1)
                self.page.lineEdit_sound.setText(arg)
                return

        super().command(arg)