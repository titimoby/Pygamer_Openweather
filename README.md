Project Pygamer_Openweather

This project is an experiment.

I took several existing projects as source of this one.
It is mainly a quick adaptation of Adafruit Code, links to those projects below.
Love you Adafruit, your products and your contents are amazing!

[Pyportal Openweather](https://github.com/adafruit/Adafruit_Learning_System_Guides/tree/master/PyPortal_OpenWeather)
[Pyportal helper library](https://github.com/adafruit/Adafruit_CircuitPython_PyPortal)
[Airlift usage](https://learn.adafruit.com/adafruit-airlift-breakout/circuitpython)
[Add esp32 as a wifi co-processor](https://learn.adafruit.com/adding-a-wifi-co-processor-to-circuitpython-esp8266-esp32)

For my tests, I used a [Esp32 DevKit v1](https://docs.zerynth.com/latest/official/board.zerynth.doit_esp32/docs/index.html)
As seen in the code, I used the following pins:

* esp32_cs = DigitalInOut(board.D10)
* esp32_ready = DigitalInOut(board.D9)
* esp32_reset = DigitalInOut(board.D6)

I will try to improve this code but I'm sure that in the end an equivalent to adafruit_pyportal library will be written for pygamer.
If I can help, you know where to find me ;)

Once more, a million thanks to Lady Ada and all fellow from Adafruit, employees and community: creativity and fun are such a wonderful thing!
