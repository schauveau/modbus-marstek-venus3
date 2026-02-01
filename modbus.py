
import time
import sys
import argparse
import logging
import ctypes
import re
import yamale
import pprint

from datetime import datetime

from pymodbus.client import ModbusTcpClient
from pymodbus.pdu import ExceptionResponse as ModbusExceptionResponse
from pymodbus.constants import ExcCodes as ModbusExcCodes

from pymodbus.exceptions import ModbusIOException
from pymodbus import (
    pymodbus_apply_logging_config,
    FramerType,
    ModbusException
)


DEFAULT_HOSTNAME="venus.private"
DEFAULT_PORT=502

YAML_INDENT=2

YAMALE_SCHEMA = yamale.make_schema(content="""
global: include('Global',required=False) 
info:    map(null(), str(), map(str(),list(str()),null()), required=False)
alias:   map(str(), list(str()), key=str(), required=False)
---
Global:
  loglevel: enum('DEBUG','INFO','WARNING','ERROR','CRITICAL', required=False)
  host: str(required=False)
  port: int(min=0,max=65535,required=False)

""")

YAMALE_TEST_CONFIG = yamale.make_data(content="""

""")

YAMALE_DEFAULT_CONFIG = yamale.make_data(content="""
global:
  loglevel: INFO
  port: 502

info:

  dummy_1:  'a comment'

  group_2:
     dummy_2: 'another comment'
  
alias:
  '@none': [] 


""")

# Will be populated with comments for the data entries
COMMENTS = {
}

ALIASES = {
}

#
# Formaters for uint16 register values  
#

def regs_to_s(*regs): 
    data = b''.join(map(lambda x: x.to_bytes(2,'big') , regs ))
    return repr(data.rstrip(b'\0'))[1:]

def r_to_b(r):
    return "0b{:016b}".format(r)
    
def r_to_u(r): 
    return str(r)
    
def r_to_i(r): 
    return str(ctypes.c_int16(r).value)

def r_to_x(r):
    return "0x{:04X}".format(r)

def rr_to_B(hi,lo):
    return "0b{:016b}{.016b}".format(hi,lo)

def rr_to_U(hi,lo):
    return str((hi<<16) + lo)

def rr_to_I(hi,lo):
    v = (hi<<16) + lo
    return ctypes.c_int32(v).value
    
def rr_to_X(hi,lo):
    return "0x{:08X}{:08X}".format(hi,lo)


#
# Describe the supported formatters to display the register values.
#
# The key must a single character.
#
# The value is a tupple ( PACKED, SIZE, CONVERTER ) where
#   - PACKED  
#     - if True the pack multiple times SIZE registers together.       
#   - SIZE is the required number of registers 
#   - CONVERTER is a callable that takes SIZE, or a multiple of SIZE if PACKED, uint16 arguments 
#     and returns the desired string representation.
#
FORMATTERS = {
    # Lowercase for single registers
    'b': ( False, 1 , r_to_b ),
    'i': ( False, 1 , r_to_i ),
    's': ( True,  1 , regs_to_s ),
    'u': ( False, 1 , r_to_u ),
    'x': ( False, 1 , r_to_x ),
    # Use uppercase for multi-registers
    'B': ( False, 2 , rr_to_B ),
    'I': ( False, 2 , rr_to_I ),
    'U': ( False, 2 , rr_to_U ),
    'X': ( False, 2 , rr_to_X ),
}


class ModbusEntry:
    
    def __init__( self, size, code, repeat=1):
        self.size = size
        self.code = code
        self.repeat = repeat

    def __repr__( self ):
        if self.repeat>1:
            return f"ModbusEntry<{self.size},{self.code},{self.repeat}>"
        else:
            return f"ModbusEntry<{self.size},{self.code}>"
            
        
#
# Describe a range of registers 
#
class ModbusSpec:    
    
    def __init__( self, kind, start, count, fmt, elems ):
        
        self.kind  = kind
        self.start = start
        self.count = count
        self.fmt   = fmt
        self.elems = elems
        
    def __repr__(self):
        return f"ModbusSpec<{self.name()}>"

    def name(self):
        return f"{self.start}_{self.count}.{self.fmt}" 
    
    # Parse a register range specification into a ModbusSpec object
    @staticmethod
    def parse(spec):
        #
        # A valid range specification must be 
        #
        # - A KIND character followed by START[_COUNT][.FORMAT]
        #   - KIND:
        #     - h for holding registers (16bit)
        #     - i for input registers (16bit) - NOT IMPLEMENTED
        #     - c for coils (1 bit) - NOT IMPLEMENTED
        #     - d for discrete inputs (1bit) - NOT IMPLEMENTED
        #   - START is a valid decimal address for the specified KIND
        #   - COUNT is a positive count (default 1)
        #   - FORMAT is a format pattern (default 'u')

        m = re.match(r'^([hicd])(\d+)(?:_(\d+))?(?:|\.(.*))$' , spec)

        if not m:
            raise Exception(f"Malformed range specification '{spec}'")            

        kind, start, count, fmt = m.group(1,2,3,4)

        if kind == 'i':
            raise Exception(f"Sorry! Input Registers are not yet implemented")
        if kind == 'c':
            raise Exception(f"Sorry! Coils are not yet implement")
        if kind == 'd':
            raise Exception(f"Sorry! Discrete Inputs are not yet implemented")
        
        start=int(start,0)
        if start not in range(0,0x10000):
            raise ValueError(f"Invalid address in '{spec}'")

        if count is None or count=='':
            count=None
        else:
            count=int(count,0)
            if count not in range(1,0x10000):
                raise ValueError(f"Invalid size in '{spec}'")

        if fmt is None or fmt=='':
            fmt='u'

        #### And expand the fmt into a list of elements

        elems = ModbusSpec.expand_(fmt, count)
        elems_count = sum(map(lambda x: x.size*x.repeat, elems))
        if count is None:
            # Implicit count: Use what is required to complete the format 
            count = elems_count
        else:
            # Explicit count: If not enough in elems then extend it by
            #    increasing the repeat count of its last element
            if elems_count < count:
                last = elems[-1]
                nb = (count-elems_count+last.size-1)//last.size 
                last.repeat = last.repeat + nb

        return ModbusSpec(kind, start, count, fmt, elems)

    #
    # Read the register range using the specified modbus client
    # and return a list of tupples (see apply_format)
    #
    def read(self, client):

        if self.kind=='h':
            ans = read_holding_registers(client, self.start, self.count)
        else:
            raise Exception(f"Data '{self.kind}' is not implemented")
        
        if ans.isError():
            error_value = f"Modbus '{modbus_exception_name(ans.exception_code)}'"
            return [ ( self.count, error_value , '?' ) ]
        else:
            return self.apply_format(ans.registers)


    #
    # Expand a format specification specification 
    # 
    #  - fmt    : a str containing the format specification
    #  - remain : the number of available modbus entities or None if unknown
    #
    # Return a list of ModbusEntry
    #
    @staticmethod
    def expand_(fmt, available):
        loop  = None
        elems = []
        
        count=0
        for c in fmt:
            if c.isdigit():
                count=count*10+int(c)
                if count>=65536:
                    print(f"Unexpected large number in format '{fmt}'")
                    sys.exit(1)
                continue
            if not c in FORMATTERS:
                print(f"Unsupported character '{c}' in data specification")
                sys.exit(1)

            packed, size, converter = FORMATTERS[c]

            if packed:
                repeat = 1
                if count==0:
                    # Consume all remaining registers
                    if type(available) is int:
                        count = (available+size-1) // size
                size = count*size
            else:
                if count==0:
                    count=1
                repeat = count
                
            entry = ModbusEntry(size, c, repeat)

            elems.append( entry )

            if type(available) is int:
                available = available - size*repeat
                if available<=0:
                    break

            count=0


        if not elems:
            elems.append( ModbusEntry(1,'u',1) )

        return elems


    # Apply this format to a list of register values
    #
    # The result is a list of tupples (COUNT, TEXT, FMT)
    # 
    #  - COUNT is a number of registers 
    #  - TEXT  is the textual representation for those COUNT registers
    #  - FMT   is the format code used to create that textual representation (e.g. 'u', 's', ...) 
    #
    # The sum of all COUNT will match len(values)
    #
    def apply_format(self, rvalues):

        results = [] 
        # elems = ModbusSpec.decompose_group_format(self.fmt)

        if len(rvalues) != self.count :
            raise Exception(f"Illegal number of registers: Got {len(rvalues)} but expected {self.count}")

        i=0 
        remain=len(rvalues)        
        for elem in self.elems:
            for k in range(elem.repeat):
                if elem.size > len(rvalues)-i :
                    value = "TRUNCATED"
                elif elem.code in FORMATTERS:
                    formatter = FORMATTERS[elem.code][2]
                    value = formatter( *rvalues[i:i+elem.size] )
                else:
                    raise Exception(f"Unexpected format code '{elem.code}'")
                i=i+elem.size
                results.append( (elem.size, str(value) , elem.code ) )    

        return results

def modbus_connect(config):

    if args.marstek_fix:
        packet_filter = marstek_packet_correction
    else:
        packet_filter = None

    config_globals = config['global'] 
                
    client = ModbusTcpClient(config_global['host'],
                             port=config_global['port'],
                             timeout=2.0,
                             retries=2,
                             trace_packet=packet_filter )
    client.connect()

    if args.marstek_info:
        marstek_print_info(client)
    
    return client

    
    
#
# Scale an int value and, if possible, without changing its type
#
# Reminder: in python, 0/1 = 0.0 so a 'float' 
#
def scale(v, mul=None, div=None):
    if type(v) == int:
        if mul is not None:
            v=v*mul
        if div is not None:
            v=v/div
    return v

def read_holding_registers(client, reg, count):
    return client.read_holding_registers(reg, count=count)

def safe_read_string(client, reg, count, default=None):
    ans = client.read_holding_registers(reg, count=count)
    if ans.isError():
        return default
    else:
        return registers_to_string(ans.registers)
    
def safe_read_u16(client, reg, count=1, default=None):
    ans = client.read_holding_registers(reg, count=count)
    if ans.isError():
        return default
    else:
        return ans.registers
    
    

def modbus_exception_name(code):
    try:
        return ModbusExcCodes(code).name
    except Exception as e:
        return str(code)
    
#
# Filter to correct malformed Exception responses produced by (all?) Marstek batteries.
#  
# The byte containing the data size is 4 but shall be 3.
#
def marstek_packet_correction(sending: bool, data: bytes) -> bytes:
    if not sending:
        if len(data)==9:  # Exception responses are always 9 bytes 
            if data[5]==4:  # Shall not be 3
                if (data[7]&0x80)==0x80: # bit 7 in function code indicates an exception
                    return data[0:5] + b'\x03' + data[6:]  
    return data


def marstek_print_info(client):

    model = safe_read_string(client, 31000,4, '?')
    com   = safe_read_string(client, 30350,6, '?')
    
    #
    # I am not 100% certain that odd registers contains minor versions but that makes sense.
    # If not, they probably represent a version number for something else.
    #
    version  = safe_read_u16(client, 30200,6, ['?']*6)
    ems=f"{version[0]}.{version[1]}"
    vns=f"{version[2]}.{version[3]}"
    bms=f"{version[4]}.{version[5]}"
    
    print(f"# Marstek model={model} com={com} ems={ems} vns={vns} bms={bms}")

#
#
#
#
# def parse_register_group_spec(fmt):
#     loop  = None
#     steps = []
#     count=0 
#     for c in fmt:
#         if c.isdigit():
#             count=count*10+int(c)
#             continue
#         elif c=='*':
#             count=0
#             loop = len(s['step'])
#             continue
#         elif c==' ' or c==',':
#             continue
#         if count==0:
#             count=1 
#         if c in 'uixb':
#             repeat=count
#             code=c
#             size=1
#         elif c in 'UIXB':
#             repeat=count
#             code=c
#             size=2
#         elif c in 's':
#             repeat=1   
#             code='s'
#             size=count
#         else:
#             print(f"Unsupported character '{c}' in format")
#             sys.exit(1)
#         if count>=1000:
#             print(f"Number {count} is too large in format")
#             sys.exit(1)
#         entry=(code, size)        
#         steps.extend( [entry]*repeat )
#         count=0
#     if not steps:
#         steps.append( ('u',1) )
#     if loop is None:
#         loop = len(steps)-1  # will loop on the last element    
#     return (steps, loop)

# Convert an array of 16bit register values into a human-readable string 
def registers_to_string(values):
    data = b''.join(map(lambda x: x.to_bytes(2,'big') , values ))
    return repr(data.rstrip(b'\0'))[1:]




# def apply_format(fmt, r):
#     result = [] 
#     steps, loop = parse_register_group_spec(fmt)
#     pos=0
#     i=0
#     remain=len(r)
#     while remain>0:
#         code, size = steps[pos]
#         if size>remain:
#             value = "TRUNCATED"
#             size = remain
#         elif code in FORMATTERS:
#             formatter = FORMATTERS[code][2]
#             value = formatter( *r[i:i+size] )
#         else:
#             raise Exception(f"Unexpected formatter '{code}'")
#         pos=pos+1
#         i=i+size
#         remain=remain-size
#         # The format used for that element 
#         elem_format = str(size)+code if size!=1 else code
#         result.append( (size, str(value) , elem_format ) )        
#         if pos >= len(steps):
#             pos = loop
#     return result

def add_command_read(subparsers):
    sp = subparsers.add_parser('read', help='Read registers ranges')    
    sp.add_argument('read_ranges', metavar='RANGE', nargs='+', help='range description')
    sp.add_argument('--compact', dest='read_compact', action=argparse.BooleanOptionalAction)
    
def action_read(args, config):

    compact  = args.read_compact

    ranges = map(ModbusSpec.parse, args.read_ranges) 
            
    client = modbus_connect(config)

    for rg in ranges:
        
        print(f"# Read {rg.name()} ")
        
        address = rg.start
        for elem in rg.read(client):
            src = f"{address}_{elem[0]}.{elem[2]}"
            val = elem[1]
            print("{:12} = {}".format(src,val) )
        address = address + elem[0]
                

    client.close()

def add_command_monitor(subparsers):
    
    sp = subparsers.add_parser('monitor', help='Monitor changes in register ranges')    
    sp.add_argument('monitor_ranges', metavar='RANGE', nargs='+', help='range description')
    sp.add_argument('-d', '--delay', dest='monitor_delay', type=float, nargs='?', default=1.0 )
    sp.add_argument('-c', '--count', dest='monitor_count', type=int, nargs='?', default=0 )
    sp.add_argument('-A', '--show-all', dest='monitor_show_all', action='store_true')
    sp.add_argument('-I', '--show-iteration', dest='monitor_show_iteration', action='store_true')
    sp.add_argument('-G', '--show-group', dest='monitor_show_group', action='store_true')
    sp.add_argument('-P', '--show-previous', dest='monitor_show_previous', action='store_true')
    sp.add_argument('-T', '--show-time', dest='monitor_show_time', action='store_true')

    sp.add_argument('--ignore', dest='monitor_ignore', action='extend', nargs='*')

def action_monitor(args, config):

    speclist = expand_specifications( args.monitor_ranges, ALIASES)
    ranges = list(map(ModbusSpec.parse, speclist))
    
    count  = args.monitor_count  # Number of iterations (0 for infinite)
    delay  = args.monitor_delay  # Sleep delay after each iteration

    show_iteration = args.monitor_show_iteration
    show_group     = args.monitor_show_group
    show_all       = args.monitor_show_all
    show_previous  = args.monitor_show_previous
    show_time      = args.monitor_show_time
    
    client = modbus_connect(config)

    previous_values={} 
    
    i=0
    while True:
        if show_iteration:
            print(f"# Iteration {i+1}")
        for rg in ranges:
            if i==0 and show_group: 
                print(f"# Read {rg.name()} ")
            kind    = rg.kind
            address = rg.start
            ts = datetime.now().strftime("[%H:%M:%S] ") if show_time else '' 
            for elem in rg.read(client):
                name = f"{kind}{address}_{elem[0]}.{elem[2]}"
                value = elem[1]

                previous = previous_values.get(name,None)
                diff = previous!=value
                show = show_all or diff

                if show:
                    comment = f' # {COMMENTS[name]}' if (name in COMMENTS) else ''
                    if show_previous and diff and i>0:
                        print("{}{:12} = from {} to {:8}{}".format(ts,name,previous,value,comment) )
                    else:
                        print("{}{:12} = {:10}{}".format(ts,name,value,comment) )

                previous_values[name] = value
                
                address = address + elem[0]
        
        i=i+1 
        if i==count:
            break
        time.sleep(delay)
   
    client.close()

def add_command_scan(subparsers):
    
    sp = subparsers.add_parser('scan', help='Scan a range of registers')
    sp.add_argument('scan_start', metavar='START', type=int, help='start address')
    sp.add_argument('scan_end'  , metavar='END',   type=int, help='end address')
    sp.add_argument('scan_step' , metavar='STEP',  type=int, nargs='?', default=10, help='step between addresses (default 10)')
    sp.add_argument('-y','--yaml', dest='scan_yaml' , action='store_true', help="produce YAML output") 
    sp.add_argument('-Y','--yaml-all', dest='scan_yaml_all' , action='store_true', help="produce YAML output") 
    sp.add_argument('-p','--show-progress', dest='scan_progress' , action='store_true', help="Display progression") 
    
    
def action_scan(args, config):

    start = args.scan_start
    end   = args.scan_end
    step  = args.scan_step
    yaml_all  = args.scan_yaml_all
    yaml  = args.scan_yaml or yaml_all
    progress  = args.scan_progress

    next_progress = -1
    
    rcount=0 # register count
    bcount=0 # block count
    
    if start<0 or start>65535:
        print(f"Illegal start register {step}. Valid range is 0-65535")
        sys.exit(1)

    if end<0 or end>65535:
        print(f"Illegal end register {step}. Valid range is 0-65535")
        sys.exit(1)

    if end<start:
        print(f"End register {step} is smaller than start {start}")
        sys.exit(1)
        
    if step<=0:
        print(f"Illegal step register")
        sys.exit(1)
        
    # YAML indentation
    yam1=' '*(YAML_INDENT*1)
    yam2=' '*(YAML_INDENT*2)
        
    client = modbus_connect(config)

    config_global = config['global']
    
    if yaml:
        print("global:")
        print(f"{yam1}host: '{config_global['host']}'")
        print(f"{yam1}port: '{config_global['port']}'")
        print("info:")

    print(f"# Scan Holding Registers from {start} to {end} step {step} ")
    
    at=start
    while at<end :
        
        if progress:
            if at >= next_progress:
                print(f"# scan progress {at}")
                next_progress = at+500
                
        
        count=0
        r = read_holding_registers(client, at, count+1)
        while not r.isError() and at+count<end :
           count = count+1
           r = read_holding_registers(client, at, count+1)
        if count>0:
            rcount = rcount + count
            bcount = bcount + 1
            if yaml:
                print(f"{yam1}h{at}_{count}.{count}u:")
                print(f"{yam2}alias: '@h{at}'")
                print(f"{yam2}append: [ '@all' ]")
                if yaml_all:
                    for i in range(count):
                        print(f"{yam2}h{at+i}_1.u: 'unknown'")
                print(flush=True)

            else:
                print(f"# Found address={at} count={count}",flush=True)
        at=at+count+1

        if at % step > 0 :
            at = (at//step)*step + step

    if yaml:
        print("aliases:")
        print(f"{yam1}'@all': [ ]",flush=True)
        print(flush=True)

    print(f"# Summary: Found {rcount} registers in {bcount} blocks")
            
    client.close()

#
# The test action does nothing except connect & disconnect.
# This is a good place to add code.
#
#
def add_command_aliases(subparsers):
    sp = subparsers.add_parser('aliases', help='List all aliases')

def action_aliases(args, config):

    for name in sorted(ALIASES.keys()):
        value = ALIASES[name]
        if type(value) is str:
            print(f"{name:10} = '{value}'")
        else:
            print(f"{name:10} = {value}")
    
#
# The test action does nothing except connect & disconnect.
# This is a good place to add code.
#
#
def add_command_test(subparsers):
    sp = subparsers.add_parser('test', help='TEST')

def action_test(args, config):
    client = modbus_connect(config)

    if client.connected:
        print('OK: Connected')
        
    client.close()
    

def parse_arguments():
    
        
    return args


# Validate a configurate created with yamale.make_data()
def validate_config(what, data):

    # Note: This is not officially documented but yamale.make_data() outputs
    # a list of tuple (data,filename) where
    #   - 'data' is the data as provided by the YAML parser  
    #   - 'filename' is the filename or None 
    if type(data) is not list:
        raise Exception("INTERNAL ERROR: Bad yamale data")
    if len(data) != 1 :
        raise Exception("Found multiple YAML documents")
    if type(data[0]) is not tuple:
        raise Exception("INTERNAL ERROR: Bad yamale data")
    if len(data[0]) != 2:
        raise Exception("INTERNAL ERROR: Bad yamale data")

    try :
        yamale.validate( YAMALE_SCHEMA, data)
    except yamale.yamale_error.YamaleError as e:
        log.error(' Validation of %s failed',what)
        for result in e.results:
            for error in result.errors:
                log.error(" %s",error)
            sys.exit(1)

    return data[0][0]

#
# Populate the comments dict using config['info']
#
def populate_comments(comments, info):
    for key in sorted(info.keys()) :
        if key in [ 'alias', 'append' ]:        
            continue # ignore special keys
        v = info[key]
        if v is None:
            continue
        if type(v) is str:
            if key in comments:
                log.warning(f"Duplicate comment for '{key}'")
            # print(f"COMMENT {key} = '{v}'")
            comments[key] = v
        elif type(v) is dict:
            populate_comments(comments, v)

def add_alias(aliases, FROM, TO, append=False):
    if not FROM.startswith('@'):
        log.error(f'Alias {FROM} does not start with @. Ignoring')
        return
    
    if append:
        if FROM not in aliases:
            aliases[FROM] = []
        alias_list = aliases[FROM]
        if type(alias_list) is not list:
            log.error(f'Cannot append to non-list alias {FROM}')
            return
        aliases[FROM].append(TO)
    else:
        if FROM in aliases:
            log.error(f'Duplicate alias {FROM}')
        aliases[FROM] = TO    

#
# Return a list containing specs after expanding all the aliases
#
# specs can a string or a list of strings. 
#
def expand_specifications(specs, aliases):

    seen = []  # everything that previously encountered during expansion
    out = [] # 
    
    def _rec_expand(out,spec,seen):

        if type(spec) is str:
            if spec in seen:
                return []
            seen.append(spec)
            if spec.startswith('@'):
                if spec in aliases:
                    return _rec_expand(out, aliases[spec], seen)
                else:
                    log.error(f'Unknown alias {spec}')
            else:
                out.append(spec)
        elif type(spec) is list:
            for e in spec:
                _rec_expand(out, e, seen) 
            
    _rec_expand(out, specs, seen)
    return out

    
def get_all_aliases(config):

    aliases = {} 
    
    #
    # Aliases can be declared in config.alias either as a str or as a list of str
    #
    # alias:
    #  '@mac':  'h30304_6.s'
    #  '@':
    #
    config_alias = config.get('alias',{})
    for FROM in sorted(config_alias.keys()) :
        TO = config_alias[FROM]
        if not FROM.startswith('@'):
            log.warning(f'Alias {FROM} does not start with @. Ignoring')
            continue
        if FROM in aliases:
            log.warning(f'Duplicate alias {FROM}')
            continue
        aliases[FROM] = TO

    # In config info,
    #   - 'alias' can also be used to defined an alias for the current entry.
    #   - and 'alias_append' can append it to existing alias lists.
    #
    #  info:
    #    h30300_10.4u6s:
    #      alias: '@30300'   # '@30300' is an alias for 'h30300_10.4u6s' 
    #      alias_append: [ '@static' ]    
    #
    config_info = config.get('info',{})
    for TO in sorted(config_info.keys()):
        value = config_info[TO]
        if type(value) is dict:
            if 'alias' in value:
                FROM = value['alias']
                add_alias(aliases,FROM,  TO)
            if 'append' in value:
                from_list = value['append']
                if type(from_list) != list:
                    log.warning(f'append requires a list. Ignoring')
                    continue
                for FROM in from_list:
                    add_alias(aliases, FROM, TO, append=True)                    

    #
    # And expand all aliases
    #
    expanded_aliases = {} 
    for name, value in aliases.items():
        expanded_aliases[name] = expand_specifications(value, aliases)

    return expanded_aliases


###################################################################

try:

    parser = argparse.ArgumentParser()

    parser.add_argument('-c', '--config')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--host')
    parser.add_argument('--port', type=int)
    parser.add_argument('--marstek-fix', default=True, action=argparse.BooleanOptionalAction)
    parser.add_argument('-M','--marstek-info', action="store_true", help='Display Marstek information after connection ')

    subparsers = parser.add_subparsers(dest='command',help='subcommand help')
    add_command_scan(subparsers)
    add_command_read(subparsers)
    add_command_aliases(subparsers)
    add_command_test(subparsers)
    add_command_monitor(subparsers)
    args = parser.parse_args()

    if args.command == None :
        parser.print_help()        
        sys.exit(1)

    if args.config:
        what   = args.config
        config_data = yamale.make_data(args.config)
    else:
        what   = 'YAMALE_DEFAULT_CONFIG'
        config_data = YAMALE_DEFAULT_CONFIG

    logging.basicConfig()
    log = logging.getLogger('pymodbus')
    log.setLevel(logging.INFO)
    
    config = validate_config( what, config_data ) 
    
    populate_comments( COMMENTS, config.get('info',{}) )

    ALIASES = get_all_aliases(config)
    
    log.setLevel(logging.INFO)
    
    config['global'] = config.get('global', {} )
    config_global = config['global']
    config_global['host'] = args.host or config_global.get('host', DEFAULT_HOSTNAME)
    config_global['port'] = args.port or config_global.get('port', DEFAULT_PORT)
    
    if args.command == 'read' :
        action_read(args,config)
    elif args.command == 'read2' :
        action_read2(args,config)
    elif args.command == 'scan' :
        action_scan(args,config)
    elif args.command == 'test' :
        action_test(args,config)
    elif args.command == 'monitor' :
        action_monitor(args,config)
    elif args.command == 'aliases' :
        action_aliases(args,config)
    else:
        print("Unsupported command")
        sys.exit(1)

    sys.exit(0)

except KeyboardInterrupt:    
    pass

