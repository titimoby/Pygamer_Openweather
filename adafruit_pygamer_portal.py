import os
import time
import gc
import board
import busio
from digitalio import DigitalInOut
import pulseio
import adafruit_touchscreen
import neopixel

from adafruit_esp32spi import adafruit_esp32spi, adafruit_esp32spi_wifimanager
import adafruit_esp32spi.adafruit_esp32spi_requests as requests
try:
    from adafruit_display_text.text_area import TextArea  # pylint: disable=unused-import
    print("*** WARNING ***\nPlease update your library bundle to the latest 'adafruit_display_text' version as we've deprecated 'text_area' in favor of 'label'")  # pylint: disable=line-too-long
except ImportError:
    from adafruit_display_text.Label import Label
from adafruit_bitmap_font import bitmap_font

import storage
import adafruit_sdcard
import displayio
import audioio
import rtc
import supervisor

from adafruit_io.adafruit_io import RESTClient, AdafruitIO_RequestError

try:
    from secrets import secrets
except ImportError:
    print("""WiFi settings are kept in secrets.py, please add them there!
the secrets dictionary must contain 'ssid' and 'password' at a minimum""")
    raise

# pylint: disable=line-too-long
# you'll need to pass in an io username, width, height, format (bit depth), io key, and then url!
IMAGE_CONVERTER_SERVICE = "https://io.adafruit.com/api/v2/%s/integrations/image-formatter?x-aio-key=%s&width=%d&height=%d&output=BMP%d&url=%s"
# you'll need to pass in an io username and key
TIME_SERVICE = "https://io.adafruit.com/api/v2/%s/integrations/time/strftime?x-aio-key=%s"
# our strftime is %Y-%m-%d %H:%M:%S.%L %j %u %z %Z see http://strftime.net/ for decoding details
# See https://apidock.com/ruby/DateTime/strftime for full options
TIME_SERVICE_STRFTIME = '&fmt=%25Y-%25m-%25d+%25H%3A%25M%3A%25S.%25L+%25j+%25u+%25z+%25Z'
LOCALFILE = "local.txt"
# pylint: enable=line-too-long


class PyGamer:
    # pylint: disable=too-many-instance-attributes, too-many-locals, too-many-branches, too-many-statements
    def __init__(self, *, url=None, headers=None, json_path=None, regexp_path=None,
                 default_bg=0x000000, status_neopixel=None,
                 text_font=None, text_position=None, text_color=0x808080,
                 text_wrap=False, text_maxlen=0, text_transform=None,
                 image_json_path=None, image_resize=None, image_position=None,
                 caption_text=None, caption_font=None, caption_position=None,
                 caption_color=0x808080, image_url_path=None,
                 success_callback=None, esp=None, external_spi=None, debug=False):

        self._debug = debug

        self._url = url
        self._headers = headers
        if json_path:
            if isinstance(json_path[0], (list, tuple)):
                self._json_path = json_path
            else:
                self._json_path = (json_path,)
        else:
            self._json_path = None

        self._regexp_path = regexp_path
        self._success_callback = success_callback

        if status_neopixel:
            self.neopix = neopixel.NeoPixel(status_neopixel, 1, brightness=0.2)
        else:
            self.neopix = None
        self.neo_status(0)

        try:
            os.stat(LOCALFILE)
            self._uselocal = True
        except OSError:
            self._uselocal = False

        if self._debug:
            print("Init display")
        self.splash = displayio.Group(max_size=15)

        if self._debug:
            print("Init background")
        self._bg_group = displayio.Group(max_size=1)
        self._bg_file = None
        self._default_bg = default_bg
        self.splash.append(self._bg_group)

        # show thank you and bootup file if available
        for bootscreen in ("/thankyou.bmp", "/pyportal_startup.bmp"):
            try:
                os.stat(bootscreen)
                board.DISPLAY.show(self.splash)
                time.sleep(2)
                self.set_background(bootscreen, position=(0,0))
                board.DISPLAY.wait_for_frame()
                time.sleep(2)
            except OSError:
                pass # they removed it, skip!

        if esp:  # If there was a passed ESP Object
            if self._debug:
                print("Passed ESP32 to PyPortal")
            self._esp = esp
            if external_spi: #If SPI Object Passed
                spi = external_spi
            else:  # Else: Make ESP32 connection
                spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        else:
            if self._debug:
                print("Init ESP32")
            esp32_cs = DigitalInOut(board.D10)
            esp32_ready = DigitalInOut(board.D9)
            esp32_reset = DigitalInOut(board.D6)
            #esp32_gpio0 = DigitalInOut(board.ESP_GPIO0)
            spi = busio.SPI(board.SCK, board.MOSI, board.MISO)

            #self._esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset, esp32_gpio0)
            self._esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
        #self._esp._debug = 1
        for _ in range(3): # retries
            try:
                print("ESP firmware:", self._esp.firmware_version)
                break
            except RuntimeError:
                print("Retrying ESP32 connection")
                time.sleep(1)
                self._esp.reset()
        else:
            raise RuntimeError("Was not able to find ESP32")
        requests.set_interface(self._esp)

        if url and not self._uselocal:
            self._connect_esp()

        if self._debug:
            print("My IP address is", self._esp.pretty_ip(self._esp.ip_address))

        # set the default background
        self.set_background(self._default_bg)
        board.DISPLAY.show(self.splash)

        self._qr_group = None
        # Tracks whether we've hidden the background when we showed the QR code.
        self._qr_only = False

        if self._debug:
            print("Init caption")
        self._caption = None
        if caption_font:
            self._caption_font = bitmap_font.load_font(caption_font)
        self.set_caption(caption_text, caption_position, caption_color)

        if text_font:
            if isinstance(text_position[0], (list, tuple)):
                num = len(text_position)
                if not text_wrap:
                    text_wrap = [0] * num
                if not text_maxlen:
                    text_maxlen = [0] * num
                if not text_transform:
                    text_transform = [None] * num
            else:
                num = 1
                text_position = (text_position,)
                text_color = (text_color,)
                text_wrap = (text_wrap,)
                text_maxlen = (text_maxlen,)
                text_transform = (text_transform,)
            self._text = [None] * num
            self._text_color = [None] * num
            self._text_position = [None] * num
            self._text_wrap = [None] * num
            self._text_maxlen = [None] * num
            self._text_transform = [None] * num
            self._text_font = bitmap_font.load_font(text_font)
            if self._debug:
                print("Loading font glyphs")
            # self._text_font.load_glyphs(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
            #                             b'0123456789:/-_,. ')
            gc.collect()

            for i in range(num):
                if self._debug:
                    print("Init text area", i)
                self._text[i] = None
                self._text_color[i] = text_color[i]
                self._text_position[i] = text_position[i]
                self._text_wrap[i] = text_wrap[i]
                self._text_maxlen[i] = text_maxlen[i]
                self._text_transform[i] = text_transform[i]
        else:
            self._text_font = None
            self._text = None

        self._image_json_path = image_json_path
        self._image_url_path = image_url_path
        self._image_resize = image_resize
        self._image_position = image_position
        if image_json_path or image_url_path:
            if self._debug:
                print("Init image path")
            if not self._image_position:
                self._image_position = (0, 0)  # default to top corner
            if not self._image_resize:
                self._image_resize = (320, 240)  # default to full screen



    def neo_status(self, value):
        """The status NeoPixel.

        :param value: The color to change the NeoPixel.

        """
        if self.neopix:
            self.neopix.fill(value)

    def set_background(self, file_or_color, position=None):
        """The background image to a bitmap file.

        :param file_or_color: The filename of the chosen background image, or a hex color.

        """
        print("Set background to ", file_or_color)
        while self._bg_group:
            self._bg_group.pop()

        if not position:
            position = (0, 0)  # default in top corner

        if not file_or_color:
            return  # we're done, no background desired
        if self._bg_file:
            self._bg_file.close()
        if isinstance(file_or_color, str): # its a filenme:
            self._bg_file = open(file_or_color, "rb")
            background = displayio.OnDiskBitmap(self._bg_file)
            try:
                self._bg_sprite = displayio.TileGrid(background,
                                                     pixel_shader=displayio.ColorConverter(),
                                                     position=position)
            except TypeError:
                self._bg_sprite = displayio.TileGrid(background,
                                                     pixel_shader=displayio.ColorConverter(),
                                                     x=position[0], y=position[1])
        elif isinstance(file_or_color, int):
            # Make a background color fill
            color_bitmap = displayio.Bitmap(320, 240, 1)
            color_palette = displayio.Palette(1)
            color_palette[0] = file_or_color
            try:
                self._bg_sprite = displayio.TileGrid(color_bitmap,
                                                     pixel_shader=color_palette,
                                                     position=(0, 0))
            except TypeError:
                self._bg_sprite = displayio.TileGrid(color_bitmap,
                                                     pixel_shader=color_palette,
                                                     x=position[0], y=position[1])
        else:
            raise RuntimeError("Unknown type of background")
        self._bg_group.append(self._bg_sprite)
        board.DISPLAY.refresh_soon()
        gc.collect()
        board.DISPLAY.wait_for_frame()

    def get_local_time(self, location=None):
        # pylint: disable=line-too-long
        """Fetch and "set" the local time of this microcontroller to the local time at the location, using an internet time API.

        :param str location: Your city and country, e.g. ``"New York, US"``.

        """
        # pylint: enable=line-too-long
        self._connect_esp()
        api_url = None
        try:
            aio_username = secrets['aio_username']
            aio_key = secrets['aio_key']
        except KeyError:
            raise KeyError("\n\nOur time service requires a login/password to rate-limit. Please register for a free adafruit.io account and place the user/key in your secrets file under 'aio_username' and 'aio_key'")# pylint: disable=line-too-long

        location = secrets.get('timezone', location)
        if location:
            print("Getting time for timezone", location)
            api_url = (TIME_SERVICE + "&tz=%s") % (aio_username, aio_key, location)
        else: # we'll try to figure it out from the IP address
            print("Getting time from IP address")
            api_url = TIME_SERVICE % (aio_username, aio_key)
        api_url += TIME_SERVICE_STRFTIME
        try:
            response = requests.get(api_url)
            if self._debug:
                print("Time request: ", api_url)
                print("Time reply: ", response.text)
            times = response.text.split(' ')
            the_date = times[0]
            the_time = times[1]
            year_day = int(times[2])
            week_day = int(times[3])
            is_dst = None  # no way to know yet
        except KeyError:
            raise KeyError("Was unable to lookup the time, try setting secrets['timezone'] according to http://worldtimeapi.org/timezones")  # pylint: disable=line-too-long
        year, month, mday = [int(x) for x in the_date.split('-')]
        the_time = the_time.split('.')[0]
        hours, minutes, seconds = [int(x) for x in the_time.split(':')]
        now = time.struct_time((year, month, mday, hours, minutes, seconds, week_day, year_day,
                                is_dst))
        print(now)
        rtc.RTC().datetime = now

        # now clean up
        response.close()
        response = None
        gc.collect()

    def fetch(self, refresh_url=None):
        """Fetch data from the url we initialized with, perfom any parsing,
        and display text or graphics. This function does pretty much everything
        Optionally update the URL
        """
        if refresh_url:
            self._url = refresh_url
        json_out = None
        image_url = None
        values = []

        gc.collect()
        if self._debug:
            print("Free mem: ", gc.mem_free())  # pylint: disable=no-member

        r = None
        if self._uselocal:
            print("*** USING LOCALFILE FOR DATA - NOT INTERNET!!! ***")
            r = Fake_Requests(LOCALFILE)

        if not r:
            self._connect_esp()
            # great, lets get the data
            print("Retrieving data...", end='')
            self.neo_status((100, 100, 0))   # yellow = fetching data
            gc.collect()
            r = requests.get(self._url, headers=self._headers)
            gc.collect()
            self.neo_status((0, 0, 100))   # green = got data
            print("Reply is OK!")

        if self._debug:
            print(r.text)

        if self._image_json_path or self._json_path:
            try:
                gc.collect()
                json_out = r.json()
                gc.collect()
            except ValueError:            # failed to parse?
                print("Couldn't parse json: ", r.text)
                raise
            except MemoryError:
                supervisor.reload()

        if self._regexp_path:
            import re

        if self._image_url_path:
            image_url = self._image_url_path

        # extract desired text/values from json
        if self._json_path:
            for path in self._json_path:
                try:
                    values.append(PyPortal._json_traverse(json_out, path))
                except KeyError:
                    print(json_out)
                    raise
        elif self._regexp_path:
            for regexp in self._regexp_path:
                values.append(re.search(regexp, r.text).group(1))
        else:
            values = r.text

        if self._image_json_path:
            try:
                image_url = PyPortal._json_traverse(json_out, self._image_json_path)
            except KeyError as error:
                print("Error finding image data. '" + error.args[0] + "' not found.")
                self.set_background(self._default_bg)

        # we're done with the requests object, lets delete it so we can do more!
        json_out = None
        r = None
        gc.collect()

        if image_url:
            try:
                print("original URL:", image_url)
                image_url = self.image_converter_url(image_url,
                                                     self._image_resize[0],
                                                     self._image_resize[1])
                print("convert URL:", image_url)
                # convert image to bitmap and cache
                #print("**not actually wgetting**")
                filename = "/cache.bmp"
                chunk_size = 12000      # default chunk size is 12K (for QSPI)
                if self._sdcard:
                    filename = "/sd" + filename
                    chunk_size = 512  # current bug in big SD writes -> stick to 1 block
                try:
                    self.wget(image_url, filename, chunk_size=chunk_size)
                except OSError as error:
                    print(error)
                    raise OSError("""\n\nNo writable filesystem found for saving datastream. Insert an SD card or set internal filesystem to be unsafe by setting 'disable_concurrent_write_protection' in the mount options in boot.py""") # pylint: disable=line-too-long
                except RuntimeError as error:
                    print(error)
                    raise RuntimeError("wget didn't write a complete file")
                self.set_background(filename, self._image_position)
            except ValueError as error:
                print("Error displaying cached image. " + error.args[0])
                self.set_background(self._default_bg)
            finally:
                image_url = None
                gc.collect()

        # if we have a callback registered, call it now
        if self._success_callback:
            self._success_callback(values)

        # fill out all the text blocks
        if self._text:
            for i in range(len(self._text)):
                string = None
                if self._text_transform[i]:
                    func = self._text_transform[i]
                    string = func(values[i])
                else:
                    try:
                        string = "{:,d}".format(int(values[i]))
                    except (TypeError, ValueError):
                        string = values[i] # ok its a string
                if self._debug:
                    print("Drawing text", string)
                if self._text_wrap[i]:
                    if self._debug:
                        print("Wrapping text")
                    lines = PyPortal.wrap_nicely(string, self._text_wrap[i])
                    string = '\n'.join(lines)
                self.set_text(string, index=i)
        if len(values) == 1:
            return values[0]
        return values

    def _connect_esp(self):
        self.neo_status((0, 0, 100))
        while not self._esp.is_connected:
            # secrets dictionary must contain 'ssid' and 'password' at a minimum
            print("Connecting to AP", secrets['ssid'])
            if secrets['ssid'] == 'CHANGE ME' or secrets['ssid'] == 'CHANGE ME':
                change_me = "\n"+"*"*45
                change_me += "\nPlease update the 'secrets.py' file on your\n"
                change_me += "CIRCUITPY drive to include your local WiFi\n"
                change_me += "access point SSID name in 'ssid' and SSID\n"
                change_me += "password in 'password'. Then save to reload!\n"
                change_me += "*"*45
                raise OSError(change_me)
            self.neo_status((100, 0, 0)) # red = not connected
            try:
                self._esp.connect(secrets)
            except RuntimeError as error:
                print("Could not connect to internet", error)
                print("Retrying in 3 seconds...")
                time.sleep(3)

    def set_caption(self, caption_text, caption_position, caption_color):
        # pylint: disable=line-too-long
        """A caption. Requires setting ``caption_font`` in init!

        :param caption_text: The text of the caption.
        :param caption_position: The position of the caption text.
        :param caption_color: The color of your caption text. Must be a hex value, e.g.
                              ``0x808000``.

        """
        # pylint: enable=line-too-long
        if self._debug:
            print("Setting caption to", caption_text)

        if (not caption_text) or (not self._caption_font) or (not caption_position):
            return  # nothing to do!

        if self._caption:
            self._caption._update_text(str(caption_text))  # pylint: disable=protected-access
            board.DISPLAY.refresh_soon()
            board.DISPLAY.wait_for_frame()
            return

        self._caption = Label(self._caption_font, text=str(caption_text))
        self._caption.x = caption_position[0]
        self._caption.y = caption_position[1]
        self._caption.color = caption_color
        self.splash.append(self._caption)

    def set_text(self, val, index=0):
        """Display text, with indexing into our list of text boxes.

        :param str val: The text to be displayed
        :param index: Defaults to 0.

        """
        if self._text_font:
            string = str(val)
            if self._text_maxlen[index]:
                string = string[:self._text_maxlen[index]]
            if self._text[index]:
                # print("Replacing text area with :", string)
                # self._text[index].text = string
                # return
                try:
                    text_index = self.splash.index(self._text[index])
                except AttributeError:
                    for i in range(len(self.splash)):
                        if self.splash[i] == self._text[index]:
                            text_index = i
                            break

                self._text[index] = Label(self._text_font, text=string)
                self._text[index].color = self._text_color[index]
                self._text[index].x = self._text_position[index][0]
                self._text[index].y = self._text_position[index][1]
                self.splash[text_index] = self._text[index]
                return

            if self._text_position[index]:  # if we want it placed somewhere...
                print("Making text area with string:", string)
                self._text[index] = Label(self._text_font, text=string)
                self._text[index].color = self._text_color[index]
                self._text[index].x = self._text_position[index][0]
                self._text[index].y = self._text_position[index][1]
                self.splash.append(self._text[index])