# modbus-marstek-venus3
Tools to analyse the Marsek Venus 3 Modbus protocol  

## Introduction

This directory contains my analysis of the Marstek Venus 3 Modbus protocol and some tools I developped for that purpose: 

- `modbus.py` a Python3 script to scan and read modbus registers.

## ModbusTCP on the Marstek Venus 3

- Listen on standard port 502 on the RJ45 interface but not the WiFi.
- Can only open one connection
  - SO MAKE SURE THAT ALL OTHER TOOLS ARE STOPPED BEFORE USING `modbus.py`
- 16bit Holding registers only (no coils, no input registers, ...)
  - range 30000-39999 is read-only.
  - range 40000-49999 is writable.   
  - Consecutive registers can be read using a single request.
- There is a 150ms delay between responses so about 6 responses per second.
  - Probably hardcoded in the Venus firmware!
  - so it can be a lot faster to read groups when possible
- Reading the 2nd word of some dual registers (32bit) can cause a disconnection for a few seconds (probably a crash and an restart in the Marstek firmware).   
- The Modbus exception responses produced by the Venus 3 are malformed:
  - Packet length is 9 and the 6th byte containing the size of the remaining payload is incorrectly set to 4 instead of 3.
  - That 'bug' can introduce long delays when reading at an illegal address.
  - This is fixed in `modbus.py` with a packet filter (see `marstek_packet_correction`)

## Dependencies of modbus.py 

As of January 2026, I am using 
- Python 3.13.5 
- pymodbus 3.11.4 
- yamale   6.1.0

Reminder: On systems such as Debian where pip3 is not directly available, it is possible to create a suitable virtual environment (venv) as follow:

```
python3 -m venv /path/to/my_venv_dir
source /path/to/my_venv_dir/bin/activate
pip3 install pymodbus==3.11.4
pip3 install yamale==6.1.0
```

You then need to activate your venv directory before using it another shell or script:

```
source /path/to/my_venv_bin/activate
python3 /path/to/modbus.py --help
```

## Quick introduction to `modbus.py` 


### Test the Modbus connection with the `test` command

The Venus 3 listen on port 502 of its LAN interface (not WiFi) and only allows one connection. Make sure that no other client is running.

```
(shell) python3 modbus.py --host  192.168.0.99 test
OK: Connected
```

### Scan for readable holding registers

The valid range for modbus addresses is 0..65535 (0xFFFF) so a full scan requires that many request.


The Marstek Venus 3 is limited to one request every 150ms so a full scan would take `65535 * 0.150ms = 9830 s = 2h43m50s`. Fortunately, Marstek appears to only be using the range 30000-39999 for read-only registers and 40000-49999 for read-write registers and most groups of consecutive registers start at a multiple of 10.

For the Venus 3, the only 2 known exceptions are at
  - address 44002 of length 2
  - address 45603 of length 3

A full scan with the command `scan 30000 50000 1` would still take 50 minutes on the Venus 3 so let's start with a small but fastest `scan 30000 32000 10` that should be completed after a few seconds

```
(shell) python3 modbus.py --host 192.168.0.99 scan 30000 30200 10
### python3 /home/chauveau/git/modbus-marstek-venus3/modbus.py scan 30000 30200 10
# Scan Holding Registers from 30000 to 30200 step 10 
# Found address=30000 count=8
# Found address=30010 count=1
# Found address=30020 count=21
# Found address=30100 count=11
# Summary: Found 41 registers in 4 blocks
...
```

### Generate a YAML configuration file

This repository may already contain a YAML configuration file for your battery model. If so you can skip this step and simply edit the host IP or name in that file.  

Use the `scan` command with the option `-y, --yaml` or `-Y, --yaml-all` to perform a scan and create a skeleton YAML configuration file.

The `--yaml` option will define an alias for each group of consecutive registers found during the scan.

The `--yaml-all` option does the same but it will also add an `unknown` comment for each register.

Reminder: On the Venus 3, the 150ms delay between modbus requests means that a full scan from 30000 to 50000 will take about 50 minutes. Using a step of 10 can speed things significantlybut can miss a few registers.   

```
(shell) python3 modbus.py --host 192.168.0.99 scan --yaml-all 30000 50000 10 > config.yaml
```

# The YAML configuration file 

TO BE DOCUMENTED.

THE FORMAT IS LIKELY TO CHANGE.


# Read specifications 

The format of the read specifications is `<KIND><ADDRESS>[_<SIZE>][.<FORMAT>]` where
  - `<KIND>` is one of 
    - `h` for holding registers
    - `i' for input register (NOT IMPLEMENTED)
    - `c' for coils (NOT IMPLEMENTED)
    - `d' for discrete inputs (NOT IMPLEMENTED)
  - `<ADDRESS>` is a decimal address between 0 and 65535
  - `<SIZE>` is an optional number of registers
    - the default is to compute it from the FORMAT.
  - `<FORMAT`> is a sequence of characters describing how the values must be displayed

For registers, the characters in the `FORMAT` are interpreted as follow:

  - 'u' : an unsigned 16bit in decimal (1 register)
  - 'i' : a signed 16bit in decimal (1 register)
  - 'x' : 16 bit in hexadecimal (1 register)
  - 'U', 'I', and 'X' are similar to 'u', 'i' and 'x' but for 32bit values (2 registers)
  - an optional number followed by 's' to display an ASCII string (2 characters per register).
    - if no number is specified then all the remaining registers are used.
  - Numbers can also be used to duplicate elements with a fixed register size
    - Example: `4u` is equivalent to 'uuuu'
  - When a format is not long enough to consume all the register values then the last element is duplicated.
    - Example: `h10000_9.u2s` is equivalent to 'h10000_9.u2s2s2s2s'

Each read specification will be transformed into a sequence of final specifications. The syntax for the final specificatin is similar except that the display format can only contain one character.

# Aliases

Read specifications can be complex so aliases can be defined in the YAML configuration file.

An alias name shall start with a `@` and can contain a single specification or a list of specification. 

Aliases are expanded recursively in a way that prevents duplicate or infinite recursion. 

Within a specification described in the YAML `info` section:
  - use `alias` to define an non-modifiable alias for that specification.
  - and `append` to append this specification to a modifiable alias.

Example: 
```
info:
  
  h30010_1.1u:
    alias: '@h30010'
    append: [ '@all', '@zero' ]
    h30010_1.u: 'always 0?'    

  h35000_3.3u:
    alias: '@h35000'
    append: [ '@all', '@nonzero' , @temp ]
    h35000_1.u: 'Internal temperature (0.1°C)'
    h35001_1.u: 'MOS1 temperature (0.1°C)'
    h35002_1.u: 'MOS2 temperature (0.1°C)'

  h35110_3.3u:
    alias: '@h35110'
    append: [ '@all', '@nonzero' ]
    h35110_1.u: 'Battery Charge Voltage Limit (0.1V)'
    h35111_1.u: 'Battery Charge Current Limit (0.1A)'    # 1000 = 100A
    h35112_1.u: 'Battery Discharge Current Limit (0.1A)' # 1000 = 100A

  
```

The YAML `alias` section can also be used to create aliases:
  - Those created with a list can be modified using 'append' in 'info'
  - Those created with a string cannot be modified.

```
alias:
    '@foo': '@h35110'  # another alias for h35110_3.3u
    '@temp': [ 'h34011_6.6u' ] # all the temperatures
    '@all':  [] # append here all blocks.
    '@zero':  [] # append here all blocks that only contain zeros.
    '@nonzero':  [] #  append here blocks that contain non-zero values.   
```

# The `aliases` command

Use it to list all aliases.

With the previous examples, that could give:
```
(shell) python3 modbus.py -c config.yaml aliases
@all     = ['h30010_1.1u', 'h35000_3.3u', 'h35110_3.3u']
@foo     = ['h35110_3.3u']
#h30010  = ['h30010_1.1u']
@h35000  = ['h35000_3.3u'] 
@h35110  = ['h35110_3.3u'] 
@nonzero = ['h35000_3.3u', 'h35110_3.3u']
@temp    = ['h34011_6.6u', 'h35000_3.3u']
@zero    = ['h30010_1.1u']
```

### The `read` command

Read registers according to one or more read specifications (including aliases):

For example, read the 8 holding register at address 31000:
```
(shell) python3 modbus -c config.yaml read h31000_8
h31000_1.u   = 22094     
h31001_1.u   = 21317     
h31002_1.u   = 13101     
h31003_1.u   = 12288     
h31004_1.u   = 1543
h31005_1.u   = 0     # always 0?
h31006_1.u   = 0     # always 0?
h31007_1.u   = 0     # always 0?
```

Comments come from my YAML configuration file but only when the address, size and format are an exact match.

Here, values were displayed as unsigned decimal (i.e. format '.u'). Let's use hexadecimal for the first 5 registers:

```
(shell) python3 modbus -c config.yaml read h31000_8.5x3u
h31000_1.x   = 0x564E    
h31001_1.x   = 0x332D
h31002_1.x   = 0x332D
h31003_1.x   = 0x3000
h31004_1.u   = 0x0607    # always 0x0607? 
h31005_1.u   = 0         # always 0?        
h31006_1.u   = 0         # always 0?        
h31007_1.u   = 0         # always 0?   
```

But the first 4 registers actually contains a string so

```
(shell) python3 modbus -c config.yaml read h31000_8.4sx3u
h31000_4.s   = 'VNSE3-0'  # Model Name
h31004_1.u   = 0x0607     # always 0x0607? 
h31005_1.u   = 0          # always 0?        
h31006_1.u   = 0          # always 0?        
h31007_1.u   = 0          # always 0?   
```

My YAML configuration contains the following information for that block of registers:

```
  h31000_10.4sxuuuuu:
    alias: '@h31000'
    append: [ '@all', '@nonzero' ]
    h31000_4.s: 'Model Name'  # 'VNSE3-0' 
    h31004_1.x: 'always 0x0607' # a version number?
    h31005_1.u: 'always 0?'
    h31006_1.u: 'always 0?'
    h31007_1.u: 'always 0?'
    h31008_1.u: 'always 0?'
```

So the alias `@h31000` can also be used:

```
(shell) python3 modbus -c config.yaml read @h31000
h31000_4.s   = 'VNSE3-0'  # Model Name
h31004_1.x   = 0x0607     # always 0x0607
h31005_1.u   = 0          # always 0?
h31006_1.u   = 0          # always 0?
h31007_1.u   = 0          # always 0?
h31008_1.u   = 0          # always 0?
h31009_1.u   = 0          # always 0?
```

The option `-S, --show-spec` to display the actual read specifications.

```
(shell) python3 modbus -c config.yaml read -S @h31000
# Read 31000_10.4sxuuuuu 
h31000_4.s   = 'VNSE3-0'  # Model Name
h31004_1.x   = 0x0607     # always 0x0607
h31005_1.u   = 0          # always 0?
h31006_1.u   = 0          # always 0?
h31007_1.u   = 0          # always 0?
h31008_1.u   = 0          # always 0?
h31009_1.u   = 0          # always 0?
```


### The `monitor` command

This is an advanced version of the `read` command with the ability to iterate.

For example, this will display all changes occuring in the register block with alias '@h30000'. Use CTRL-C to stop or a non-zero count value.  

```
(shell) python3 modbus -c config.yaml monitor -c 0  -IPT @h30000
# Iteration 1
[21:20:13] h30000_1.u   = 529        # Battery Voltage (0.1V)
[21:20:13] h30001_1.i   = -3         # Signed Battery Power (W)
[21:20:13] h30002_1.u   = 192        # Temperature? (0.1°C)
[21:20:13] h30003_1.u   = 197        # Temperature? (0.1°C)
[21:20:13] h30004_1.u   = 2300       # AC Voltage (0.1V)
[21:20:13] h30005_1.u   = 7          # Backup AC Voltage (0.1V)
[21:20:13] h30006_1.i   = 0          # Signed AC Power (W)
[21:20:13] h30007_1.i   = 0          # Signed Backup Power (W)
# Iteration 2
[21:20:13] h30001_1.i   = from -3 to -2       # Signed Battery Power (W)
# Iteration 3
# Iteration 4
# Iteration 5
# Iteration 6
# Iteration 7
# Iteration 8
# Iteration 9
[21:20:14] h30001_1.i   = from -2 to -3       # Signed Battery Power (W)
[21:20:14] h30004_1.u   = from 2300 to 2302     # AC Voltage (0.1V)
[21:20:14] h30005_1.u   = from 7 to 16       # Backup AC Voltage (0.1V)
# Iteration 10
# Iteration 11
# Iteration 12
# Iteration 13
[21:20:15] h30000_1.u   = from 529 to 526      # Battery Voltage (0.1V)
[21:20:15] h30002_1.u   = from 192 to 191      # Temperature? (0.1°C)
[21:20:15] h30004_1.u   = from 2302 to 2301     # AC Voltage (0.1V)
[21:20:15] h30005_1.u   = from 16 to 6        # Backup AC Voltage (0.1V)
# Iteration 14
# Iteration 15
# Iteration 16
^C
```

See `monitor -h` for a description of the supported options.

TODO: Implement some options to write registers or execute shell commands at some iterations.
 

















