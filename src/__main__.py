from PyQt5.QtWidgets import QApplication
import sys
import logging
import re
import string
from typing import Optional

log = logging.getLogger('AVP.Main')


def main() -> int:
    """Returns an exit code (0 for success)"""
    proj: Optional[str] = None  # Use Optional[str] because proj can be None
    mode: str = 'GUI'

    # Determine whether we're in GUI or commandline mode
    if len(sys.argv) > 2:
        mode = 'commandline'
    elif len(sys.argv) == 2:
        if sys.argv[1].startswith('-'):
            mode = 'commandline'
        else:
            # remove unsafe punctuation characters such as \/?*&^%$#
            if sys.argv[1].endswith('.avp'):
                # remove file extension
                sys.argv[1] = sys.argv[1][:-4]
            sys.argv[1] = re.sub(f'[{re.escape(string.punctuation)}]', '', sys.argv[1])
            # opening a project file with gui
            proj = sys.argv[1]

    # Create Qt Application
    app = QApplication(sys.argv)
    app.setApplicationName("audio-visualizer")

    # Launch program
    if mode == 'commandline':
        from .command import Command

        main_command = Command()  # Renamed to avoid shadowing the main function
        mode = main_command.parseArgs()
        log.debug("Finished creating command object")

    # Both branches here may occur in one execution:
    # Commandline parsing could change mode back to GUI
    if mode == 'GUI':
        from .gui.mainwindow import MainWindow

        mainWindow = MainWindow(proj)
        log.debug("Finished creating MainWindow")
        mainWindow.raise_()

    return app.exec_()

if __name__ == '__main__':
    sys.exit(main())