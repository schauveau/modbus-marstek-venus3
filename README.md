# modbus-marstek-venus3
Tools to analyse the Marsek Venus 3 Modbus protocol  

## Introduction

This directory contains my analysis of the Marstek Venus 3 Modbus protocol and some tools I developped for that purpose: 

- `modbus.py` a Python3 script to scan and read modbus registers.

## ModbusTCP on the Marstek Venus 3

- Listen on standard port 502 on the RJ45 interface but not the WiFi.
- Can only open one connection.
- Holding registers only (no coils, no input registers, ...)
  - range 30000-39999 is read-only.
  - range 40000-49999 is writable.   
  - register groups start at addresses that are a multiple of 10.
- There is a 150ms delay between responses so about 6 responses per second.
  - Probably hardcoded in the Venus firmware!  
- Reading the 2nd word of a dual register (so 32bit) can cause a disconnection for a few seconds. 
  - so reading from odd addresses should be avoided.  
- The Modbus exception responses produced by the Venus 3 are malformed. 
  - The 6th byte containing the size of the remaining payload is incorrectly set to 4 instead of 3.
  - That 'bug' can introduce significant delays when reading illegal registers.  
  - Can be fixed using a packet filter (see `marstek_packet_correction` in `modbus.py`)


## Notations

This document is using the following notations

- `uint16`, `int16` and `hex16` refer to a 16bit (1 register) displayed unsigned, signed or hexadecimal.   
- `uint32`, `int32` and `hex32` refer to a 32bit (2 registers) displayed unsigned, signed or hexadecimal.   


## Dependencies of modbus.py 

As of January 2026, I am using 
- Python 3.13.5 
- pymodbus 3.11.4 

Reminder: On systems such as Debian where pip3 is not directly available, it is
possible to create a suitable virtual Python3 environment as follow:

```
python3 -m venv /path/to/my_venv_dir
source /path/to/my_venv_dir/bin/activate
pip3 install pymodbus==3.11.4
```

and then create a shell script to run `modbus.py`  

```bash
#!/bin/bash
source /path/to/my_venv_bin/activate
python3 /path/to/modbus.py --host 192.168.0.99 "$@"
```

## Quick introduction to `modbus.py` 

It is assumed that a `modbus` shell command has be properly declared.

### Test the Modbus connection   

Reminder: Only one client and only using the LAN interface.

```
# alias modbus="python3 /path/to/modbus.py --host  192.168.0.99"
# modbus test
OK: Connected
```

For convenience, you can edit the `DEFAULT_HOSTNAME` variable in `modbus.py` or create a shell script or shell alias to pass the required options.

In the following examples, I will assume that a script called `modbus` is provided.  

### Find readable register groups within a specific range

```
# modbus scan 31000 34000
Register scan from 31000 to 34000 step 10 
Found address=31000 count=10
Found address=32100 count=15
Found address=32200 count=5
Found address=32300 count=3
Found address=33000 count=12
Summary: Found 45 registers in 5 blocks
```

### Read the 10 registers at address 31000 

The default is to display each register as a uint16.

```
# modbus read 31000:10
31000[1] = 22094
31001[1] = 21317
31002[1] = 13101
31003[1] = 12288
31004[1] = 1543
31005[1] = 0
31006[1] = 0
31007[1] = 0
31008[1] = 0
31009[1] = 0
```

Use the `--format` option to control how the values must be displayed (see below for more details) 

```
# modbus read "31000:10:4s,x,5u"
31000[4] = 'VNSE3-0\x00'
31004[1] = 0x0607
31005[1] = 0
31006[1] = 0
31007[1] = 0
31008[1] = 0
31009[1] = 0
```

All registers in the specified range must exist:  

```
# modbus read "31000:12"
31000[12] = Modbus Error Response 'ILLEGAL_ADDRESS'
```

## Register range 

A register range is of the START, START:SIZE or START:SIZE:FORMAT where START is a valid register address (0-65535), SIZE is a number of registers (default to 1) and FORMAT specifies how the registers must be displayed. 

A FORMAT is composed of a sequence of characters.


- `u` for a uint16 
- `i` for a int16
- `x` for a hex16
- `U` for a uint32
- `I` for a int32
- `X` for a hex32
- `s` to interpret a register as an ascii string (2 characters per register).
- a non-zero positive number 
  - before `s` to specify the number of registers to use (e.g `5s` for a string of 10 characters)
  - before `uixUIX` to repeat it (e.g. `5u` is equivalent to `uuuuu`) 
- The last characters after the `*` are repeated (e.g. `u*x2i` is equivalent to `uxiixiixiixiixiixiixii...`)- If the format does not contain any `*` then the last element is repeated (e.g. `5i2xu` is equivalent to `5i2x*u` or `5i2xuuuuuuuuuuu...`)
- `,` are ignored but can be freely inserted to improve the readability.

Here are a few examples using imaginary ranges of registers.

Two int16 followed by some hex32:
```
# modbus read "20000:8:2iX" 
20000[1] = -42      
20001[1] = -123   
20002[2] = 0xABADCAFE
20004[2] = 0x00000000
20006[3] = 0x11112222
```

The format can be longer than the number of register:
```
# modbus read "20000:2:2iXuu"
20000[1] = -42
20001[1] = -123
```

But if not enough registers are present then the last displayed element may be truncated.

In the following example, the trailing `u` are ignored but the 'X' is truncated (32bit for only 1 register) 

```
# modbus read "20000:3:2iXuu"
20000[1] = -42      
20001[1] = -123
20002[1] = TRUNCATED
```

Here, all registers are 16 bits: First a single int16 then a repeated sequence of hex16 and uint16. 

```
# modbus read "20000:8:i*xu"
20000[1] = -42      
20001[1] = 0x0000   
20002[1] = 65535
20003[1] = 0xFFFF
20004[1] = 42
20005[1] = 0xABCD 
20006[1] = 12
20007[1] = 0xEEEE
```

The same output can also be obtained with the following formats

```
# modbus read "20000:8:ixuxuxux"
or
# modbus read "20000:8:i,x,u,x,u,x,u,x"
```


Here, the first 5 registers contain a string of 10 characters and the remaining 16bit registers are all displayed in hexadecimal. The comma is optional. 
```
# modbus read 20000 8 --format '5s,x'
20000[5] = 'HelloWorld'
20005[1] = 0x12E4
20006[1] = 0x0023
20007[1] = 0xFFFF
```







