import threading

from imswitch.imcommon.framework import Signal, SignalInterface
from imswitch.imcommon.model import initLogger
from .RotatorManager import RotatorManager


class ESP32RotatorManager(RotatorManager, SignalInterface):
    """ RotatorManager for a rotation stage driven by the "A" (fourth) motor
    axis of a UC2 ESP32 board. It reuses the very same UC2-REST connection
    that ESP32StageManager uses (``lowLevelManagers['rs232sManager']``, keyed
    by the ``rs232device`` manager property), so an ESP32Manager entry in the
    ``rs232devices`` section of the setup file is required. Written for
    napari-lsft ("Route B") so the OPT / rotator widgets can drive the UC2
    rotation axis for tomographic acquisition.

    Manager properties:

    - ``rs232device`` (`str`): name of the ESP32 RS232 device defined in the
      ``rs232devices`` section of the setup file (e.g. ``"ESP32"``).
    - ``stepsPerTurn`` (`int`): number of motor (micro)steps per full
      revolution; conversion factor between steps and angle. Default: 3200
      (200 full steps x 16 microsteps).
    - ``startSpeed`` (`int`): motor speed in steps/s. Default: 15000.
    - ``acceleration`` (`int`): motor acceleration in steps/s^2; ``None``
      uses the firmware default. Default: None.
    - ``stepSizeA`` (`float`): physical step size factor forwarded to the
      UC2-REST motor object for the A axis; keep at 1 so that positions stay
      in raw steps. Default: 1.
    - ``backlashA`` (`int`): backlash compensation in steps. Default: 0.
    - ``moveTimeout`` (`float`): timeout in seconds for a single (blocking)
      move on the device. Default: 30.
    - ``asyncMove`` (`bool`): if True (default), moves are executed in a
      background thread and ``sigRotatorPositionUpdated`` is emitted when the
      move has finished (same asynchronous semantics as the Telemetrix
      rotator, which the OPT scan workflow relies on). If False, moves block
      the calling thread; do not use False for OPT scans, as the emitted
      signal would recursively trigger the next scan step in the same stack.

    Positions are tracked in software (like ESP32StageManager does): the
    device is commanded with absolute step positions and the last commanded
    target is stored. ``position()`` reports degrees in [0, 360).
    """

    sigRotatorPositionUpdated = Signal()

    def __init__(self, rotatorInfo, name, **lowLevelManagers):
        # The (UC2) RotatorManager base class is a plain ABC that does not
        # initialize SignalInterface; initialize it explicitly first so that
        # the class-level signal is usable. (In ImSwitch versions where the
        # base class already derives from SignalInterface this is redundant
        # but harmless, as no connections exist yet at construction time.)
        SignalInterface.__init__(self)
        RotatorManager.__init__(self, rotatorInfo, name)
        self.__logger = initLogger(self, instanceName=name)

        if rotatorInfo is None:
            self.__logger.error('No rotator info provided in the configuration file')
            raise ValueError('No rotator info provided in the configuration file')
        properties = rotatorInfo.managerProperties

        # Same connection mechanism as ESP32StageManager: the ESP32Manager
        # RS232 device exposes the UC2-REST client, whose .motor object talks
        # to the firmware motor endpoints.
        self._rs232manager = lowLevelManagers['rs232sManager'][
            properties['rs232device']
        ]
        self._motor = self._rs232manager._esp32.motor

        self._stepsPerTurn = properties.get('stepsPerTurn', 3200)
        self.speed = properties.get('startSpeed', properties.get('speed', 15000))
        self.acceleration = properties.get('acceleration', None)
        self._stepSizeA = properties.get('stepSizeA', 1)
        self._backlashA = properties.get('backlashA', 0)
        self._moveTimeout = properties.get('moveTimeout', 30)
        self._asyncMove = properties.get('asyncMove', True)

        self._moveLock = threading.Lock()  # serializes device moves
        self._currentSteps = 0

        # Make sure the A axis of the shared motor object is configured even
        # if no ESP32StageManager manages it (mirrors setupMotor there).
        try:
            self._motor.setup_motor(axis='A', minPos=None, maxPos=None,
                                    stepSize=self._stepSizeA,
                                    backlash=self._backlashA)
        except Exception as e:
            self.__logger.warning(f'Could not setup A axis on device: {e}')

        # Try to read the boot-up position of the A axis from the device
        # (UC2-REST returns positions ordered (A, X, Y, Z)).
        try:
            self._currentSteps = int(self._motor.get_position()[0])
        except Exception as e:
            self.__logger.warning(f'Could not read A axis position, assuming 0: {e}')
            self._currentSteps = 0
        self._position = self._stepsToAngle(self._currentSteps)

        self.__logger.info(
            f'Initialized ESP32 rotator on A axis of '
            f'{properties["rs232device"]} ({self._stepsPerTurn} steps/turn)')

    ##############
    # Conversion #
    ##############
    def _angleToSteps(self, angle):
        """ Convert an angle in degrees to motor steps. """
        return int(round(angle / 360 * self._stepsPerTurn))

    def _stepsToAngle(self, steps):
        """ Convert motor steps to an angle in degrees, wrapped to [0, 360). """
        return (steps % self._stepsPerTurn) / self._stepsPerTurn * 360

    ############
    # Position #
    ############
    def position(self):
        """ Return the current position as a float (degrees). """
        return self._position

    def get_position(self):
        """ Return the position as a tuple of (steps, angle). """
        return (self._currentSteps, self._position)

    ############
    # Movement #
    ############
    def move_rel(self, angle, inSteps=False):
        """ Move by the specified angle (degrees), or by raw motor steps if
        inSteps is True. """
        steps = int(round(angle)) if inSteps else self._angleToSteps(angle)
        self._startMove(steps, is_absolute=False)

    def move_abs(self, value, inSteps=False):
        """ Move to the specified absolute angle (degrees), or to an absolute
        motor step position if inSteps is True (the OPT scan workflow calls
        this with inSteps=True and step values in [0, stepsPerTurn)). """
        steps = int(round(value)) if inSteps else self._angleToSteps(value)
        self._startMove(steps, is_absolute=True)

    def _startMove(self, steps, is_absolute):
        if self._asyncMove:
            thread = threading.Thread(target=self._doMove,
                                      args=(steps, is_absolute),
                                      daemon=True)
            thread.start()
        else:
            self._doMove(steps, is_absolute)

    def _doMove(self, steps, is_absolute):
        """ Executes a (blocking) move on the device, updates the software
        position and emits sigRotatorPositionUpdated when finished. """
        with self._moveLock:
            try:
                self._motor.move_a(steps, self.speed,
                                   acceleration=self.acceleration,
                                   is_absolute=is_absolute,
                                   is_blocking=True,
                                   timeout=self._moveTimeout)
            except Exception as e:
                self.__logger.error(f'A axis move failed: {e}')
                return
            if is_absolute:
                self._currentSteps = steps
            else:
                self._currentSteps += steps
            self._position = self._stepsToAngle(self._currentSteps)
        self.sigRotatorPositionUpdated.emit()

    ######################################
    # RotatorController (widget) support #
    ######################################
    def set_rot_speed(self, speed):
        """ Set the rotation speed (steps/s), callable from the
        RotatorController. """
        self.speed = speed

    def set_zero_pos(self):
        """ Define the current position as zero, on the device and in
        software. """
        try:
            self._motor.set_position(axis='A', position=0)
        except Exception as e:
            self.__logger.error(f'Could not zero A axis on device: {e}')
        self._currentSteps = 0
        self._position = 0
        self.sigRotatorPositionUpdated.emit()

    def start_cont_rot(self):
        """ Start continuous rotation of the A axis. """
        self._motor.move_forever(speed=(self.speed, 0, 0, 0), is_stop=False)

    def stop_cont_rot(self):
        """ Stop continuous rotation. The position is not tracked during
        continuous rotation, so it is redefined as zero (like the Telemetrix
        rotator does). """
        try:
            self._motor.stop(axis='A')
        except Exception:
            self._motor.move_forever(speed=(self.speed, 0, 0, 0), is_stop=True)
        self.set_zero_pos()

    def set_sync_in_pos(self, abs_pos_deg):
        """ Hardware sync-in triggering is not supported by the UC2 ESP32
        firmware; provided for RotatorController compatibility. """
        self.__logger.warning('set_sync_in_pos is not supported by the ESP32 rotator')

    ###########
    # Cleanup #
    ###########
    def close(self):
        """ The ESP32 connection is owned by the shared ESP32Manager RS232
        device, so nothing must be closed here; just stop the motor. """
        try:
            self._motor.stop(axis='A')
        except Exception:
            pass

    def finalize(self):
        self.close()


# Copyright (C) 2020-2026 ImSwitch developers
# This file is part of ImSwitch.
#
# ImSwitch is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ImSwitch is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
