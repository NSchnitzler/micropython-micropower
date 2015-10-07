# upower.py Enables access to functions useful in low power Pyboard projects
# Copyright 2015 Peter Hinch
# V0.1 7th October 2015

import pyb, stm, os
rtc = pyb.RTC()

def buildcheck(tupTarget):
    fail = True
    if 'uname' in dir(os):
        datestring = os.uname()[3]
        date = datestring.split(' on')[1]
        idate = tuple([int(x) for x in date.split('-')])
        fail = idate < tupTarget
    if fail:
        raise OSError('This driver requires a firmware build dated {:4d}-{:02d}-{:02d} or later'.format(*tupTarget))

buildcheck((2015,10,7)) # Bug in earlier versions made lpdelay() unpredictable.

@micropython.asm_thumb
def ctz(r0):                                    # Count the trailing zeros in an integer
    rbit(r0, r0)
    clz(r0, r0)

def lpdelay(ms, usb_connected = False):         # Low power delay. Note stop() kills USB
    if usb_connected:
        pyb.delay(ms)
        return
    rtc.wakeup(ms)
    pyb.stop()
    rtc.wakeup(None)

class BkpRAM(object):
    BKPSRAM = 0x40024000
    def __init__(self):
      stm.mem32[stm.RCC + stm.RCC_APB1ENR] |= 0x10000000 # PWREN bit
      stm.mem32[stm.PWR + stm.PWR_CR] |= 0x100 # Set the DBP bit in the PWR power control register
      stm.mem32[stm.RCC +stm.RCC_AHB1ENR]|= 0x40000 # enable BKPSRAMEN
      stm.mem32[stm.PWR + stm.PWR_CSR] |= 0x200 # BRE backup register enable bit
    def __getitem__(self, idx):
        assert idx >= 0 and idx <= 0x3ff, "Index must be between 0 and 1023"
        return stm.mem32[self.BKPSRAM + idx * 4]
    def __setitem__(self, idx, val):
        assert idx >= 0 and idx <= 0x3ff, "Index must be between 0 and 1023"
        stm.mem32[self.BKPSRAM + idx * 4] = val
    def get_bytearray(self):
        return uctypes.bytearray_at(self.BKPSRAM, 4096)

bkpram = BkpRAM()

class RTC_Regs(object):
    def __getitem__(self, idx):
        assert idx >= 0 and idx <= 19, "Index must be between 0 and 19"
        return stm.mem32[stm.RTC + stm.RTC_BKP0R+ idx * 4]
    def __setitem__(self, idx, val):
        assert idx >= 0 and idx <= 19, "Index must be between 0 and 19"
        stm.mem32[stm.RTC + stm.RTC_BKP0R + idx * 4] = val

rtcregs = RTC_Regs()

class Tamper(object):
    def __init__(self):
        self.edge_triggered = False
        self.triggerlevel = 0
        self.tampmask = 0
        self.disable()                          # Ensure no events occur until we're ready
        self.setup()

    def setup(self, level = 0, *, freq = 16, samples = 2, edge = False):
        self.tampmask = 0
        if level == 1:
            self.tampmask |= 2
            self.triggerlevel = 1
        elif level == 0:
            self.triggerlevel = 0
        else:
            raise ValueError("level must be 0 or 1")
        if type(edge) == bool:
            self.edge_triggered = edge
        else:
            raise ValueError("edge must be True or False")
        if not self.edge_triggered:
            if freq in (1,2,4,8,16,32,64,128):
                self.tampmask |= ctz(freq) << 8
            else:
                raise ValueError("Frequency must be 1, 2, 4, 8, 16, 32, 64 or 128Hz")
            if samples in (2, 4, 8):
                self.tampmask |= ctz(samples) << 11
            else:
                raise ValueError("Number of samples must be 2, 4, or 8")

    def disable(self):
        stm.mem32[stm.RTC + stm.RTC_TAFCR] = self.tampmask

    def wait_inactive(self, usb_connected = False):
        if not self.edge_triggered:
            tamper_pin = pyb.Pin(pyb.Pin.board.X18, pyb.Pin.IN, pull = pyb.Pin.PULL_UP)
            while tamper_pin.value() == self.triggerlevel: # Wait for pin to go logically off
                lpdelay(50, usb_connected)

    def enable(self):
        BIT21 = 1 << 21                                 # Tamper mask bit
        self.disable()
        stm.mem32[stm.EXTI + stm.EXTI_IMR] |= BIT21     # Set up ext interrupt
        stm.mem32[stm.EXTI + stm.EXTI_RTSR] |= BIT21    # Rising edge
        stm.mem32[stm.EXTI + stm.EXTI_PR] |= BIT21      # Clear pending bit

        stm.mem32[stm.RTC + stm.RTC_ISR] &= 0xdfff      # Clear tamp1f flag
        stm.mem32[stm.PWR + stm.PWR_CR] |= 2            # Clear power wakeup flag WUF
        stm.mem32[stm.RTC + stm.RTC_TAFCR] = self.tampmask | 5 # Tamper interrupt enable and tamper1 enable

tamper = Tamper()

class wakeup_X1(object):                                # Support wakeup on low-high edge on pin X1
    def __init__(self):
        self.disable()
    def enable(self):                                   # In this mode pin has pulldown enabled
        stm.mem32[stm.PWR + stm.PWR_CR] |= 4            # set CWUF to clear WUF in PWR_CSR
        stm.mem32[stm.PWR + stm.PWR_CSR] |= 0x100       # Enable wakeup
    def disable(self):
        stm.mem32[stm.PWR + stm.PWR_CSR] &= 0xfffffeff  # Disable wakeup

wup_X1 = wakeup_X1()

# Return the reason for a wakeup event. Note that boot detection uses the last word of backup RAM.

def why():
    if bkpram[1023] != 0x27288a6f:
        bkpram[1023] = 0x27288a6f
        return 'BOOT'
    rtc_isr = stm.mem32[stm.RTC + stm.RTC_ISR]
    if rtc_isr & 0x2000 == 0x2000:
        return 'TAMPER'
    if rtc_isr & 0x400 == 0x400:
        return 'WAKEUP'
    return 'X1'