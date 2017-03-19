import threading
from enum import Enum
from math import modf
from .connection import YncaConnection, YncaProtocolStatus
import logging

logger = logging.getLogger(__name__)


class YncaReceiver:
    _all_zones = ["MAIN", "ZONE2", "ZONE3", "ZONE4"]

    # Map subunits to input names, this is used for discovering what inputs are available
    # Inputs missing because unknown what subunit they map to: NET
    _subunit_input_mapping = {
        "TUN": "TUNER",
        "SIRIUS": "SIRIUS",
        "IPOD": "iPod",
        "BT": "Bluetooth",
        "RHAP": "Rhapsody",
        "SIRIUSIR": "SIRIUS InternetRadio",
        "PANDORA": "Pandora",
        "NAPSTER": "Napster",
        "PC": "PC",
        "NETRADIO": "NET RADIO",
        "IPODUSB": "iPod (USB)",
        "UAW": "UAW",
    }

    # Inputs that are only available on the main unit
    _main_only_inputs = ["HDMI1", "HDMI2", "HDMI3", "HDMI4", "HDMI5", "HDMI6", "HDMI7", "AV1", "AV2", "AV3", "AV4"]

    def __init__(self, port):
        self._initialized_event = threading.Event()

        self.modelname = None
        self.firmware_version = None
        self.zones = {}
        self._zones_to_initialize = []
        self.inputs = {}
        self._connection = YncaConnection(port, self._connection_update)
        self._connection.connect()

        self._initialize_device()

    def _initialize_device(self):
        """ Communicate with the device to setup initial state and discover capabilities """
        logger.info("Receiver initialization start.")
        self._connection.get("SYS", "MODELNAME")

        # Get userfriendly names for inputs (also allows detection of available inputs)
        # Note that these are not all inputs, just the external ones it seems
        self._connection.get("SYS", "INPNAME")

        # A device also can have a number of 'internal' inputs like the Tuner, USB, Napster etc..
        # There is no way to get which of there inputs are supported by the device so just try all that we know of
        for subunit in YncaReceiver._subunit_input_mapping:
            self._connection.get(subunit, "AVAIL")

        # There is no way to get which zones are supported by the device to just try all possible
        # The callback will create any zone instances on success responses
        for zone in YncaReceiver._all_zones:
            self._connection.get(zone, "AVAIL")

        self._connection.get("SYS", "VERSION")  # Use version as a "sync" command
        if not self._initialized_event.wait(10):  # Each command is 100ms (at least) and a lot are sent\
            logger.error("Receiver initialization phase 1 failed!")

        # Initialize the zones (constructors are synchronous)
        for zone in self._zones_to_initialize:
            logger.info("Initializing zone {}.".format(zone))
            self.zones[zone] = YncaZone(zone, self._connection)
            self.zones[zone].initialize()
        self._zones_to_initialize = None

        logger.info("Receiver initialization done.")

    def _connection_update(self, status, subunit, function, value):
        if status == YncaProtocolStatus.OK:
            if subunit == "SYS":
                self._update(function, value)
            elif subunit in self.zones:
                self.zones[subunit].update(function, value)
            elif subunit in YncaReceiver._all_zones:
                self._zones_to_initialize.append(subunit)

            elif function == "AVAIL":
                if subunit in YncaReceiver._subunit_input_mapping:
                    self.inputs[YncaReceiver._subunit_input_mapping[subunit]] = YncaReceiver._subunit_input_mapping[subunit]

    def _update(self, function, value):
        if function == "MODELNAME":
            self.modelname = value
        elif function == "VERSION":
            self.firmware_version = value
            self._initialized_event.set()
        elif function.startswith("INPNAME"):
            input_id = function[7:]
            self.inputs[input_id] = value


def number_to_string_with_stepsize(value, decimals, stepsize):

    steps = round(value / stepsize)
    stepped_value = steps * stepsize
    after_the_point, before_the_point = modf(stepped_value)

    after_the_point = abs(after_the_point * (10 ** decimals))

    return "{}.{}".format(int(before_the_point), int(after_the_point))


class Mute(Enum):
    on = 1
    att_minus_20 = 2
    att_minus_40 = 3
    off = 4

DspSoundPrograms = [
    "Hall in Munich",
    "Hall in Vienna",
    "Chamber",
    "Cellar Club",
    "The Roxy Theatre",
    "The Bottom Line",
    "Sports",
    "Action Game",
    "Roleplaying Game",
    "Music Video",
    "Standard",
    "Spectacle",
    "Sci-Fi",
    "Adventure",
    "Drama",
    "Mono Movie",
    "2ch Stereo",
    "7ch Stereo",
    "Surround Decoder"]


class YncaZone:
    def __init__(self, zone, connection):
        self._initialized_event = threading.Event()
        self.subunit = zone
        self._connection = connection

        self.name = None
        self._input = None
        self._power = False
        self._volume = None
        self.max_volume = 16.5
        self._mute = None
        self._dsp_sound_program = None
        self._scenes = {}

        self._handler_cache = {}

    def initialize(self):
        """
        Initialize the Zone based on capabilities of the device.
        This is a long running function!
        """
        self._get("BASIC")  # Gets PWR, SLEEP, VOL, MUTE, INP, STRAIGHT, ENHANCER and SOUNDPRG (if applicable)
        self._get("MAXVOL")
        self._get("SCENENAME")
        self._get("ZONENAME")

        if not self._initialized_event.wait(2):  # Each command takes at least 100ms + big margin
            logger.error("Zone initialization failed!")

    def __str__(self):
        output = []
        for key in self.__dict__:
            output.append("{key}='{value}'".format(key=key, value=self.__dict__[key]))

        return '\n'.join(output)

    def _put(self, function, value):
        self._connection.put(self.subunit, function, value)

    def _get(self, function):
        self._connection.get(self.subunit, function)

    def update(self, function, value):
        if function not in self._handler_cache:
            self._handler_cache[function] = getattr(self, "_handle_{}".format(function.lower()), None)

        handler = self._handler_cache[function]
        if handler is not None:
            handler(value)
        else:
            if len(function) == 10 and function.startswith("SCENE") and function.endswith("NAME"):
                scene_id = int(function[5:6])
                self._scenes[scene_id] = value

    def _handle_inp(self, value):
        self._input = value

    def _handle_vol(self, value):
        self._volume = float(value)

    def _handle_maxvol(self, value):
        self.max_volume = float(value)

    def _handle_mute(self, value):
        if value == "Off":
            self._mute = Mute.off
        elif value == "Att -20dB":
            self._mute = Mute.att_minus_20
        elif value == "Att -40dB":
            self._mute = Mute.att_minus_40
        else:
            self._mute = Mute.on

    def _handle_pwr(self, value):
        if value == "On":
            self._power = True
        else:
            self._power = False

    def _handle_zonename(self, value):
        self.name = value
        self._initialized_event.set()

    def _handle_soundprg(self, value):
        self._dsp_sound_program = value

    @property
    def on(self):
        """Get current on state"""
        return self._power

    @on.setter
    def on(self, value):
        """Turn on/off zone"""
        assert value in [True, False]  # Is this usefull?
        self._put("PWR", "On" if value is True else "Standby")

    @property
    def muted(self):
        """Get current mute state"""
        return self._mute

    @muted.setter
    def muted(self, value):
        """Mute"""
        assert value in Mute  # Is this usefull?
        command_value = "On"
        if value == Mute.off:
            command_value = "Off"
        elif value == Mute.att_minus_40:
            command_value = "Att -40 dB"
        elif value == Mute.att_minus_20:
            command_value = "Att -20 dB"
        self._put("MUTE", command_value)

    @property
    def volume(self):
        """Get current volume in dB"""
        return self._volume

    @volume.setter
    def volume(self, value):
        """Set volume in dB. The receiver only works with 0.5 increments. Input values will be round."""
        self._put("VOL", number_to_string_with_stepsize(value, 1, 0.5))

    def volume_up(self, step_size=0.5):
        """
        Increase the volume with given stepsize.
        Supported stepsizes are: 0.5, 1, 2 and 5
        """
        value = "Up"
        if step_size in [1, 2, 5]:
            value = "Up {} dB".format(step_size)
        self._put("VOL", value)

    def volume_down(self, step_size=0.5):
        """
        Decrease the volume with given stepsize.
        Supported stepsizes are: 0.5, 1, 2 and 5
        """
        value = "Down"
        if step_size in [1, 2, 5]:
            value = "Down {} dB".format(step_size)
        self._put("VOL", value)

    @property
    def input(self):
        """Get current input"""
        return self._input

    @input.setter
    def input(self, value):
        """Set input"""
        self._put("INP", value)

    @property
    def dsp_sound_program(self):
        """Get the current DSP sound program"""
        return self._dsp_sound_program

    @dsp_sound_program.setter
    def dsp_sound_program(self, value):
        """Set the DSP sound program"""
        if value in DspSoundPrograms:
            self._put("SOUNDPRG", value)
        else:
            raise ValueError("Soundprogram not in DspSoundPrograms")

    def activate_scene(self, scene_id):
        """Activate a scene"""
        if len(self._scenes) == 0:
            raise ValueError("Zone does not support scenes")
        elif scene_id not in [1, 2, 3, 4]:
            raise ValueError("Invalid scene ID, should et 1, 2, 3 or 4")
        else:
            self._put("SCENE=Scene {}", scene_id)
