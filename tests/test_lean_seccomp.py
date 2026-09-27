"""Check both syscall ABIs and enforce the native policy in an isolated process."""
import struct
import subprocess
import sys

import pytest

from nima_semantica import lean_project


def evaluate(policy, arch, syscall, flags=0):
    packet = struct.pack('<IIQ6Q', syscall, arch, 0, flags, 0, 0, 0, 0, 0)
    instructions = list(struct.iter_unpack('HBBI', policy))
    pc = accumulator = 0
    for _ in range(len(instructions)):
        opcode, yes, no, value = instructions[pc]
        if opcode == 0x20:
            accumulator = struct.unpack_from('<I', packet, value)[0]
        elif opcode == 0x15:
            pc += yes if accumulator == value else no
        elif opcode == 0x45:
            pc += yes if accumulator & value else no
        elif opcode == 0x06:
            return value
        else:
            raise AssertionError(opcode)
        pc += 1
    raise AssertionError('Filter did not terminate')


@pytest.mark.parametrize('machine,arch,clone,blocked', [
    ('x86_64', 0xC000003E, 56, [57,58,435,101,311,272,308,165,166,428,429,430,431,432,442]),
    ('aarch64', 0xC00000B7, 220, [435,117,271,97,268,40,39,428,429,430,431,432,442]),
])
def test_policy_rejects_processes_and_privileged_operations(monkeypatch,machine,arch,clone,blocked):
    monkeypatch.setattr(lean_project.platform,'machine',lambda:machine)
    policy=lean_project._seccomp()
    assert evaluate(policy, arch ^ 1, 0) == 0x80000000
    assert evaluate(policy, arch, 0x40000000) == 0x80000000
    for syscall in blocked:
        assert evaluate(policy,arch,syscall) == 0x50000 | 38
    assert evaluate(policy,arch,clone,17) == 0x50000 | 1
    assert evaluate(policy,arch,clone,0x10000 | 0x100 | 0x800) == 0x7FFF0000
    assert evaluate(policy,arch,63 if machine=='aarch64' else 0) == 0x7FFF0000


def test_unknown_architecture_is_rejected(monkeypatch):
    monkeypatch.setattr(lean_project.platform,'machine',lambda:'armv7l')
    with pytest.raises(ValueError,match='syscall policy'):
        lean_project._seccomp()


@pytest.mark.skipif(sys.platform!='linux',reason='Linux seccomp required')
def test_native_filter_allows_threads_but_rejects_fork():
    script = '''import ctypes, errno, os, threading
from nima_semantica.lean_project import _seccomp
class Filter(ctypes.Structure):
    _fields_ = [('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [('len',ctypes.c_ushort),('filter',ctypes.POINTER(Filter))]
policy=_seccomp()
filters=(Filter*(len(policy)//8)).from_buffer_copy(policy)
program=Program(len(filters),filters)
libc=ctypes.CDLL(None,use_errno=True)
assert libc.prctl(38,1,0,0,0)==0
assert libc.prctl(22,2,ctypes.byref(program),0,0)==0
seen=[]
thread=threading.Thread(target=lambda:seen.append(True));thread.start();thread.join()
assert seen==[True]
try:
    pid=os.fork()
except OSError as error:
    assert error.errno in (errno.ENOSYS,errno.EPERM)
else:
    if pid==0: os._exit(0)
    os.waitpid(pid,0)
    raise AssertionError('process creation was permitted')
print('threads allowed; process creation denied')
'''
    result=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'process creation denied' in result.stdout
