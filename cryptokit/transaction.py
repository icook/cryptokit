from __future__ import unicode_literals
from future.builtins import bytes, range, chr
from future.builtins.types import newbytes

from hashlib import sha256
from struct import pack
from collections import namedtuple
from binascii import hexlify

from . import BitcoinEncoding
from .base58 import address_bytes
from .dark import ser_string
from .bitcoin.script import create_push_script


class Input(namedtuple('Input',
                       ['prevout_hash', 'prevout_idx', 'script_sig', 'seqno'])):
    """ Previous hash needs to be given as a byte array in little endian.
    script_sig is a byte string. Others are simply integers. """
    @classmethod
    def coinbase(cls, height, addtl_push=None, extra_script_sig=b''):
        if not addtl_push:
            addtl_push = []
        # Meet BIP 34 by adding the height of the block
        # encode variable length integer
        data = create_push_script([height] + addtl_push)
        return cls(Transaction._nullprev,
                   4294967295,
                   data + extra_script_sig, 0)


class Output(namedtuple('Output', ['amount', 'script_pub_key'])):
    """ script_pub_key is a byte string. Amount is an integer. """
    @classmethod
    def to_address(cls, amount, address):
        """ Creates an output with a script_pub_key that sends the funds to a
        specific address. Address should be given as a base58 string. """
        raw_addr = address_bytes(address)
        return cls(amount, b'\x76\xa9\x14' + raw_addr + b'\x88\xac')


class Transaction(BitcoinEncoding):
    """ An object wrapper for a bitcoin transaction. More information on the
    raw format at https://en.bitcoin.it/wiki/Transactions. """
    _nullprev = b'\0' * 32

    def __init__(self, raw=None, fees=None, disassemble=False, pos=False, messages=False):
        # raw transaction data in byte format
        if raw:
            if not isinstance(raw, (bytearray, newbytes.newbytes)):
                raise AttributeError("Raw data must be a bytestring, not {}"
                                     .format(type(raw)))
            self._raw = bytes(raw)
        else:
            self._raw = None
        self.inputs = []
        self.outputs = []
        self.locktime = 0

        if pos:
            self.n_time = 0
        else:
            self.n_time = None

        # integer value, not encoded in the pack but for utility
        self.fees = fees
        self.version = None
        # stored as le bytes
        self._hash = None
        self.transaction_message = b"" if messages else None
        if disassemble:
            self.disassemble()

    def disassemble(self, raw=None, dump_raw=False, fees=None):
        """ Unpacks a raw transaction into its object components. If raw
        is passed here it will set the raw contents of the object before
        disassembly. Dump raw will mark the raw data for garbage collection
        to save memory. """
        if fees:
            self.fees = fees
        if raw:
            self._raw = bytes(raw)
        data = self._raw

        # first four bytes, little endian unpack
        self.version = self.funpack('<L', data[:4])

        # decode the number of inputs and adjust position counter
        input_count, data = self.varlen_decode(data[4:])

        # loop over the inputs and parse them out
        self.inputs = []
        for i in range(input_count):
            # get the previous transaction hash and it's output index in the
            # previous transaction
            prevout_hash = data[:32]
            prevout_idx = self.funpack('<L', data[32:36])
            # get length of the txn script
            ss_len, data = self.varlen_decode(data[36:])
            script_sig = data[:ss_len]  # get the script
            # get the sequence number
            seqno = self.funpack('<L', data[ss_len:ss_len + 4])

            # chop off the this transaction from the data for next iteration
            # parsing
            data = data[ss_len + 4:]

            # save the input in the object
            self.inputs.append(
                Input(prevout_hash, prevout_idx, script_sig, seqno))

        output_count, data = self.varlen_decode(data)
        self.outputs = []
        for i in range(output_count):
            amount = self.funpack('<Q', data[:8])
            # length of scriptPubKey, parse out
            ps_len, data = self.varlen_decode(data[8:])
            pk_script = data[:ps_len]
            data = data[ps_len:]
            self.outputs.append(
                Output(amount, pk_script))

        self.locktime = self.funpack('<L', data[:4])
        # reset hash to be recacluated on next grab
        self._hash = None
        # ensure no trailing data...
        assert len(data) == 4
        if dump_raw:
            self._raw = None

        return self

    @property
    def is_coinbase(self):
        """ Is the only input from a null prev address, indicating coinbase?
        Technically we could do more checks, but I believe bitcoind doesn't
        check more than the first input being null to count it as a coinbase
        transaction. """
        return self.inputs[0].prevout_hash == self._nullprev

    def assemble(self, split=False):
        """ Reverse of disassemble, pack up the object into a byte string raw
        transaction. split=True will return two halves of the transaction ,
        first chunck will be up until then end of the sigscript, second chunk
        is the remainder. For changing extronance, split off the sigscript """

        # Set a default version before assembling
        if self.version is None:
            if self.transaction_message is not None:
                self.version = 2
            else:
                self.version = 1

        data = pack(str('<L'), self.version)
        if self.n_time is not None:
            data += pack(str("<i"), self.n_time)
        split_point = None

        data += self.varlen_encode(len(self.inputs))
        for prevout_hash, prevout_idx, script_sig, seqno in self.inputs:
            data += prevout_hash
            data += pack(str('<L'), prevout_idx)
            data += self.varlen_encode(len(script_sig))
            data += script_sig
            split_point = len(data)
            data += pack(str('<L'), seqno)

        data += self.varlen_encode(len(self.outputs))
        for amount, script_pub_key in self.outputs:
            data += pack(str('<Q'), amount)
            data += self.varlen_encode(len(script_pub_key))
            data += script_pub_key

        data += pack(str('<L'), self.locktime)

        if self.transaction_message is not None:
            data += ser_string(self.transaction_message)

        self._raw = data
        # reset hash to be recacluated on next grab
        self._hash = None
        if split:
            return data[:split_point], data[split_point:]
        return data

    @property
    def raw(self):
        if self._raw is None:
            self.assemble()
        return self._raw

    @property
    def hash(self):
        """ Compute the hash of the transaction when needed """
        if self._hash is None:
            self._hash = sha256(sha256(self._raw).digest()).digest()[::-1]
        return self._hash
    lehash = hash

    @property
    def behash(self):
        return self.hash[::-1]

    @property
    def lehexhash(self):
        return hexlify(self.hash)

    @property
    def behexhash(self):
        return hexlify(self.hash[::-1])

    def __hash__(self):
        return self.funpack('i', self.hash)

    def to_dict(self):
        return {'inputs': [{'prevout_hash': hexlify(inp[0]),
                            'prevout_idx': inp[1],
                            'script_sig': hexlify(inp[2]),
                            'seqno': inp[3]} for inp in self.inputs],
                'outputs': [{'amount': out[0],
                             'script_pub_key': hexlify(out[1])}
                            for out in self.outputs],
                'data': hexlify(self._raw),
                'locktime': self.locktime,
                'version': self.version,
                'hash': self.lehexhash}
