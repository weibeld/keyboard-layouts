#!/bin/env python
'''
Convert macOS keyboard layout files (.keylayout) to
equivalent Windows files (.klc).
'''

import os
import re
import sys
import time

import argparse
import codecs
import unicodedata

import xml.etree.ElementTree as ET

# local modules
from klc_data import (
    win_to_mac_keycodes, win_keycodes,
    klc_keynames, klc_prefix_dummy, klc_suffix_dummy
)
from locale_data import (
    locale_id, locale_id_long, locale_tag, locale_name, locale_name_long,
)

error_msg_conversion = (
    'Could not convert composed character {}, '
    'inserting replacement character ({}). Sorry.'
)
error_msg_filename = (
    'Too many digits for a Windows-style (8+3) filename. '
    'Please rename the source file.')

error_msg_macwin_mismatch = (
    "// No equivalent macOS code for Windows code {} ('{}'). Skipping.")

error_msg_winmac_mismatch = (
    "// Could not match Windows code {} ('{}') to Mac OS code {}. Skipping.")


# Change the line separator.
# This is important, as the output klc file must be UTF-16 LE with
# Windows-style line breaks.
os.linesep = '\r\n'

# Placeholder character for replacing 'ligatures' (more than one character
# mapped to one key), which are not supported by this conversion script.
replacement_char = '007E'


class Key(object):

    def __init__(self, keymap_set, key_index, key_code, key_type, result):

        self.keymap_set = keymap_set
        self.key_index = key_index
        self.key_code = key_code
        self.key_type = key_type
        self.result = result

    def data(self):

        self.output = [
            str(self.keymap_set),
            int(self.key_index),
            int(self.key_code),
            str(self.key_type),
            self.result]

        return self.output


class Action(object):

    def __init__(self, action, state, action_type, result):
        self.action = action
        self.state = state
        self.action_type = action_type
        self.result = result

    def data(self):
        output = [
            self.action,
            str(self.state),
            str(self.action_type),
            self.result
        ]
        return output


class KeylayoutParser(object):

    def __init__(self):
        # Raw keys as they are in the layout XML
        self.key_list = []

        # Raw list of actions collected from layout XML
        self.action_list = []

        # Key output when state is None
        self.output_list = []

        # Contains action IDs and the actual base keys (e.g. 'a', 'c' etc.)
        self.action_basekeys = {}

        # {states : deadkeys}
        self.deadkeys = {}

        # {deadkey: (basekey, output)}
        self.key_dict = {}

        # A dict of dicts, collecting the outputs of every key
        # in each individual state.
        self.output_dict = {}

        # Actions that do not yield immediate output, but shift to a new state.
        self.empty_actions = []

        # {keymap ID: modifier key}
        self.keymap_assignments = {}

        self.number_of_keymaps = 0

    def checkSet(self, states, keymap, maxset, minset, string):
        '''
        Assign index numbers to the different shift states, by comparing
        them to the minimum and maximum possible modifier configurations.
        This is necessary as the arrangement in the Mac keyboard layout
        is arbitrary.
        '''

        if maxset.issuperset(states) and minset.issubset(states):
            self.keymap_assignments[string] = int(keymap)

    def parse(self, tree):

        idx_list = []  # Find the number of key indexes.

        default_max = set('command? caps?'.split())
        default_min = set(''.split())

        alt_max = set('anyOption caps? command?'.split())
        alt_min = set('anyOption'.split())

        shift_max = set('anyShift caps? command?'.split())
        shift_min = set('anyShift'.split())

        altshift_max = set('anyShift anyOption caps? command?'.split())
        altshift_min = set('anyShift anyOption'.split())

        cmd_max = set('command caps? anyShift? anyOption?'.split())
        cmd_min = set('command'.split())

        caps_max = set('caps anyShift? command?'.split())
        caps_min = set('caps'.split())

        cmdcaps_max = set('command caps anyShift?'.split())
        cmdcaps_min = set('command caps'.split())

        shiftcaps_max = set('anyShift caps anyOption?'.split())
        shiftcaps_min = set('anyShift caps'.split())

        for parent in tree.getiterator():

            if parent.tag == 'keyMapSelect':
                for child in parent:
                    idx = int(parent.get('mapIndex'))
                    idx_list.append(idx)

                    keymap = parent.get('mapIndex')
                    states = set(child.get('keys').split())
                    self.checkSet(
                        states, keymap, default_max, default_min, 'default')
                    self.checkSet(
                        states, keymap, shift_max, shift_min, 'shift')
                    self.checkSet(
                        states, keymap, alt_max, alt_min, 'alt')
                    self.checkSet(
                        states, keymap, altshift_max, altshift_min, 'altshift')
                    self.checkSet(
                        states, keymap, cmd_max, cmd_min, 'cmd')
                    self.checkSet(
                        states, keymap, caps_max, caps_min, 'caps')
                    self.checkSet(
                        states, keymap, cmdcaps_max, cmdcaps_min, 'cmdcaps')
                    self.checkSet(
                        states, keymap,
                        shiftcaps_max, shiftcaps_min, 'shiftcaps')

            if parent.tag == 'keyMapSet':
                keymapset_id = parent.attrib['id']
                for keymap in parent:
                    keymap_index = keymap.attrib['index']
                    for key in keymap:
                        key_code = key.attrib['code']
                        if key.get('action') is None:
                            key_type = 'output'
                        else:
                            key_type = 'action'
                        output = key.get(key_type)
                        myKey = Key(
                            keymapset_id, keymap_index,
                            key_code, key_type, output)
                        self.key_list.append(myKey.data())

            if parent.tag == 'actions':
                for action in parent:
                    action_id = action.get('id')
                    for action_state in action:
                        if action_state.get('next') is None:
                            action_type = 'output'
                        else:
                            action_type = 'next'
                        state = action_state.get('state')
                        result = action_state.get(action_type)
                        myAction = Action(
                            action_id, state, action_type, result)
                        self.action_list.append(myAction.data())

                        # Make a dictionary for key id to output.
                        # On the Mac keyboard, the 'a' for instance is often
                        # matched to an action, as it can produce
                        # agrave, aacute, etc.
                        if [state, action_type] == ['none', 'output']:
                            self.action_basekeys[action_id] = result

        # Yield the highest index assigned to a shift state - thus, the
        # number of shift states in the layout.
        self.number_of_keymaps = max(idx_list)

    def findDeadkeys(self):
        '''
        Return dictionary self.deadkeys: contains the state id and the Unicode
        value of actual dead key.
        (for instance, 's3': '02c6' - state 3: circumflex)
        Returns list of ids for 'empty' actions:
        this is for finding the ids of all key inputs that have
        no immediate output. This list is used later when an '@' is appended
        to the Unicode values, a Windows convention to mark dead keys.
        '''

        deadkey_id = 0
        key_list = []
        for [key_id, state, key_type, result] in self.action_list:
            if [state, key_type, result] == ['none', 'output', '0020']:
                deadkey_id = key_id
            if key_id == deadkey_id and result != '0020':
                self.deadkeys[state] = result

            if [state, key_type] == ['none', 'next']:
                key_list.append([key_id, result])
                self.empty_actions.append(key_id)

        for i in key_list:
            if i[1] in list(self.deadkeys.keys()):
                i[1] = self.deadkeys[i[1]]

        # Add the actual deadkeys (grave, acute etc)
        # to the dict action_basekeys
        self.action_basekeys.update(dict(key_list))

        return self.empty_actions
        return self.deadkeys

    def matchActions(self):
        '''
        Return a list and a dictionary:

        self.action_list is extended by the base character, e.g.

        [
            '6', # action id
            's1',  # state
            'output',  # type
            '00c1',  # Á
            '0041'  # A
        ]

        self.action_basekeys are all the glyphs that can be combined
        with a dead key, e.g. A,E,I etc.

        '''

        for i in self.action_list:
            if [i[1], i[2]] == ['none', 'output']:
                self.action_basekeys[i[0]] = i[3]

            if i[0] in list(self.action_basekeys.keys()):
                i.append(self.action_basekeys[i[0]])

        return self.action_list
        return self.action_basekeys

    def findOutputs(self):
        '''
        Find the real output values of all the keys, e.g. replacing the
        action IDs in the XML keyboard layout with the Unicode values they
        actually return in their standard state.
        '''

        for i in self.key_list:
            if i[4] in self.empty_actions:
                # If the key is a real dead key, mark it.
                # This mark is used in 'makeOutputDict'.
                i.append('@')

            if i[4] in self.action_basekeys:
                i[3] = 'output'
                i[4] = self.action_basekeys[i[4]]
                self.output_list.append(i)
            else:
                self.output_list.append(i)

        return self.output_list

    def makeDeadKeyTable(self):
        '''
        Populate self.key_dict, which maps a deadkey
        e.g. (02dc, circumflex) to (base character, accented character) tuples
        e.g. 0041, 00c3 = A, Ã
        '''

        for i in self.action_list:
            if i[1] in list(self.deadkeys.keys()):
                i.append(self.deadkeys[i[1]])

            if len(i) == 6:
                deadkey = i[5]
                basekey = i[4]
                result = i[3]
                if deadkey in self.key_dict:
                    self.key_dict[deadkey].append((basekey, result))
                else:
                    self.key_dict[deadkey] = [(basekey, result)]

        return self.key_dict

    def makeOutputDict(self):
        '''
        This script is configurated to work for the first keymap set of an
        XML keyboard layout only.
        Here, the filtering occurs:
        '''

        first_keymapset = self.output_list[0][0]
        for i in self.output_list:
            if i[0] != first_keymapset:
                self.output_list.remove(i)
            key_id = i[2]

            li = []
            for i in range(self.number_of_keymaps + 1):
                li.append([i, '-1'])
                self.output_dict[key_id] = dict(li)

        for i in self.output_list:
            keymap_set = i[0]
            keymap_id = i[1]
            key_id = i[2]

            if len(i) == 5:
                output = i[4]
            else:
                # The string for making clear that this key is a deadkey.
                # Necessary in .klc files.
                output = i[4] + '@'

            self.output_dict[key_id][keymap_id] = output

        return self.output_dict

    def getOutput(self, key_output_dict, state):
        '''
        Used to find output per state, for every key.
        If no output, return '-1' (a.k.a. not defined).
        '''

        try:
            output = key_output_dict[self.keymap_assignments[state]]
        except KeyError:
            output = '-1'
        return output

    def writeKeyTable(self):
        output = []
        for win_kc_hex, win_kc_name in sorted(win_keycodes.items()):
            win_kc_int = int(win_kc_hex, 16)

            if win_kc_int not in win_to_mac_keycodes:
                print(error_msg_macwin_mismatch.format(
                    win_kc_int, win_keycodes[win_kc_hex]))
                continue

            mac_kc = win_to_mac_keycodes[win_kc_int]
            if mac_kc not in self.output_dict:
                print(error_msg_winmac_mismatch.format(
                    win_kc_int, win_keycodes[win_kc_hex], mac_kc))
                continue

            outputs = self.output_dict[mac_kc]

            # Keytable follows the syntax of the .klc file.
            # The columns are as follows:

            # keytable[0]: scan code
            # keytable[1]: virtual key
            # keytable[2]: spacer (empty)
            # keytable[3]: caps (on or off, or SGCaps flag)
            # keytable[4]: output for default state
            # keytable[5]: output for shift
            # keytable[6]: output for ctrl (= cmd on mac)
            # keytable[7]: output for ctrl-shift (= cmd-caps lock on mac)
            # keytable[8]: output for altGr (= ctrl-alt)
            # keytable[9]: output for altGr-shift (= ctrl-alt-shift)
            # keytable[10]: descriptions.

            keytable = list((win_kc_hex, win_kc_name)) + ([""] * 9)

            default_output = self.getOutput(outputs, 'default')
            shift_output = self.getOutput(outputs, 'shift')
            alt_output = self.getOutput(outputs, 'alt')
            altshift_output = self.getOutput(outputs, 'altshift')
            caps_output = self.getOutput(outputs, 'caps')
            cmd_output = self.getOutput(outputs, 'cmd')
            cmdcaps_output = self.getOutput(outputs, 'cmdcaps')
            shiftcaps_output = self.getOutput(outputs, 'shiftcaps')

            # Check if the caps lock output equals the shift key,
            # to set the caps lock status.
            if caps_output == default_output:
                keytable[3] = '0'
            elif caps_output == shift_output:
                keytable[3] = '1'
            else:
                # SGCaps are a Windows speciality, necessary if the caps lock
                # state is different from shift.
                # Usually, they accommodate an alternate writing system.
                # SGCaps + Shift is possible, boosting the available
                # shift states to 6.
                keytable[3] = 'SGCap'

            keytable[4] = default_output
            keytable[5] = shift_output
            keytable[6] = cmd_output
            keytable[7] = cmdcaps_output
            keytable[8] = alt_output
            keytable[9] = altshift_output
            keytable[10] = '// %s, %s, %s, %s, %s' % (
                char_description(default_output),
                char_description(shift_output),
                char_description(cmd_output),
                char_description(alt_output),
                char_description(altshift_output))  # Key descriptions

            output.append('\t'.join(keytable))

            if keytable[3] == 'SGCap':
                output.append('-1\t-1\t\t0\t%s\t%s\t\t\t\t\t// %s, %s' % (
                    caps_output,
                    shiftcaps_output,
                    char_description(caps_output),
                    char_description(shiftcaps_output)))
        return output

    def writeDeadKeyTable(self):
        '''
        Write a summary of dead keys, their results in all intended
        combinations.
        '''

        output = ['']
        for i in list(self.key_dict.keys()):
            output.extend([''])
            output.append('DEADKEY\t%s' % i)
            output.append('')

            for j in self.key_dict[i]:
                string = '%s\t%s\t// %s -> %s' % (
                    j[0], j[1], char_from_hex(j[0]), char_from_hex(j[1]))
                output.append(string)
        return output

    def writeKeynameDead(self):
        # List of dead keys contained in the keyboard layout.

        output = ['', 'KEYNAME_DEAD', '']
        for i in list(self.deadkeys.values()):
            output.append('%s\t"%s"' % (i, char_description(i)))
        output.append('')

        if len(output) == 4:
            return ['', '']
        else:
            return output


def read_file(path):
    '''
    Read a file, make list of the lines, close the file.
    '''

    with open(path, 'r') as f:
        data = f.read().splitlines()
    return data


def codepoint_from_char(character):
    '''
    Return a 4 or 5-digit Unicode hex string for the passed character.
    '''

    try:
        return '{0:04x}'.format(ord(character))

        # For now, 'ligatures' (2 or more code points assigned to one key)
        # are not supported in this conversion script.
        # Ligature support on Windows keyboards is spotty (no ligatures in
        # Caps Lock states, for instance), and limited to four code points
        # per key. Used in very few keyboard layouts only, the decision was
        # made to insert a placeholder instead.

    except TypeError:
        print(error_msg_conversion.format(
            character, char_description(replacement_char)))
        return replacement_char

    except ValueError:
        print(error_msg_conversion.format(
            character, char_description(replacement_char)))
        return replacement_char


def char_from_hex(hex_string):
    '''
    Return character from a Unicode code point.
    '''

    # XXX what is this here for?
    if len(hex_string) > 5:
        return hex_string
    else:
        return chr(int(hex_string, 16))


def char_description(hex_string):
    '''
    Return description of characters, e.g. 'DIGIT ONE', 'EXCLAMATION MARK' etc.
    '''
    if hex_string in ['-1', '']:
        return '<none>'
    hex_string = hex_string.rstrip('@')

    try:
        return unicodedata.name(char_from_hex(hex_string))
    except ValueError:
        return 'PUA {}'.format(hex_string)


def filter_xml(input_keylayout):
    '''
    Filter xml-based keylayout file.
    Unicode entities (&#x0000;) make the Elementtree xml parser choke,
    that’s why some replacement operations are necessary.
    Also, all literal output characters are converted to code points
    (0000, ffff, 1ff23 etc) for easier handling downstream.
    '''

    rx_uni_lig = re.compile(r'((&#x[a-fA-F0-9]{4};){2,})')
    rx_hex_escape = re.compile(r'&#x([a-fA-F0-9]{4,6});')
    rx_output_line = re.compile(r'(output=[\"\'])(.+?)([\"\'])')

    # Fixing the first line to make Elementtree not stumble
    # over a capitalized XML tag
    filtered_xml = ['<?xml version="1.0" encoding="UTF-8"?>']

    for line in read_file(input_keylayout)[1:]:

        if re.search(rx_output_line, line):
            if re.search(rx_uni_lig, line):
                # More than 1 output character.
                # Not supported, so fill in replacement char instead.
                lig_characters = re.search(rx_uni_lig, line).group(1)
                print(error_msg_conversion.format(
                    lig_characters, char_description(replacement_char)))
                line = re.sub(rx_uni_lig, replacement_char.lower(), line)
            elif re.search(rx_hex_escape, line):
                # Escaped code point, e.g. &#x0020;
                # Remove everything except the code point.
                query = re.search(rx_hex_escape, line)
                codepoint = query.group(1).lower()
                line = re.sub(rx_hex_escape, codepoint, line)
            else:
                # Normal character output.
                # Replace the character by a code point
                query = re.search(rx_output_line, line)
                char_pre = query.group(1)  # output="
                character = query.group(2)
                codepoint = codepoint_from_char(character).lower()
                char_suff = query.group(3)  # "
                replacement_line = ''.join((char_pre, codepoint, char_suff))
                line = re.sub(rx_output_line, replacement_line, line)

        filtered_xml.append(line)

    return '\n'.join(filtered_xml)


def make_klc_filename(keyboard_name):
    '''
    Windows .dll files allow for 8-character file names only, which is why the
    output file name is truncated. If the input file name contains a number
    (being part of a series), this number is appended to the end of the output
    file name. If this number is longer than 8 digits, the script will gently
    ask to modify the input file name.

    Periods and spaces in the file name are not supported; MSKLC will not
    build the .dll if the .klc has any.
    This is why they are stripped here.
    '''

    # strip periods and spaces
    filename = re.sub(r'[. ]', '', keyboard_name)

    # find digit(s) at tail of file name
    rx_digit = re.compile(r'(\d+?)$')
    match_digit = rx_digit.search(filename)

    if match_digit:
        trunc = 8 - len(match_digit.group(1)) - 1
        if trunc < 0:
            print(error_msg_filename)
            sys.exit(-1)
        else:
            filename = '{}_{}.klc'.format(
                filename[:trunc], match_digit.group(1))
    else:
        filename = '{}.klc'.format(filename[:8])
    return filename


def process_input_keylayout(input_keylayout):
    filtered_xml = filter_xml(input_keylayout)
    tree = ET.XML(filtered_xml)

    keyboard_data = KeylayoutParser()
    keyboard_data.parse(tree)
    keyboard_data.findDeadkeys()
    keyboard_data.matchActions()
    keyboard_data.findOutputs()
    keyboard_data.makeDeadKeyTable()
    keyboard_data.makeOutputDict()

    return keyboard_data


def make_klc_metadata(keyboard_name):

    # company = 'Adobe Systems Incorporated'
    company = 'myCompany'
    year = time.localtime()[0]

    klc_prefix = klc_prefix_dummy.format(
        locale_tag, keyboard_name, year, company, company,
        locale_name, locale_id_long)
    klc_suffix = klc_suffix_dummy.format(
        locale_id, keyboard_name, locale_id, locale_name_long)
    return klc_prefix, klc_suffix


def make_keyboard_name(input_path):
    '''
    Return the base name of the .keylayout file
    '''
    input_file = os.path.basename(input_path)
    return os.path.splitext(input_file)[0]


def verify_input_file(parser, input_file):
    '''
    Check if the input file exists, and if the suffix is .keylayout

    https://stackoverflow.com/a/15203955
    '''
    if not os.path.exists(input_file):
        parser.error('This input file does not exist')

    suffix = os.path.splitext(input_file)[-1]

    if suffix.lower() != '.keylayout':
        parser.error('Please use a xml-based .keylayout file')
    return input_file

def get_args():

    parser = argparse.ArgumentParser(
        description=__doc__)

    parser.add_argument(
        'input',
        type=lambda input_file: verify_input_file(parser, input_file),
        help='input .keylayout file'
    )

    parser.add_argument(
        '-o', '--output_dir',
        help='output directory',
        metavar='DIR',
    )

    return parser.parse_args()


if __name__ == '__main__':
    args = get_args()
    input_file = args.input

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.abspath(os.path.dirname(input_file))

    keyboard_data = process_input_keylayout(input_file)

    keyboard_name = make_keyboard_name(input_file)
    klc_prefix, klc_suffix = make_klc_metadata(keyboard_name)
    klc_filename = make_klc_filename(keyboard_name)

    output = []
    output.extend(klc_prefix.splitlines())
    output.extend(keyboard_data.writeKeyTable())
    output.extend(keyboard_data.writeDeadKeyTable())
    output.extend(klc_keynames)
    output.extend(keyboard_data.writeKeynameDead())
    output.extend(klc_suffix.splitlines())

    output_path = os.sep.join((output_dir, klc_filename))
    with codecs.open(output_path, 'w', 'utf-16') as output_file:
        for line in output:
            output_file.write(line)
            output_file.write(os.linesep)

    print(f'written {keyboard_name} to {klc_filename}')
