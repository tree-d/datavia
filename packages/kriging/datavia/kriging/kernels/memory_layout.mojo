"""Pointer types and the vector width shared by the kriging kernels.

The kernels read the caller's arrays in place through raw pointers. A pointer
stored in a struct field cannot carry the caller's origin, so the kernels keep
"untracked" pointers: the caller guarantees that the memory outlives the call.
"""

from std.memory import Pointer
from std.sys import simd_width_of

comptime F64Ptr = Pointer[Float64, MutUntrackedOrigin]
"""Pointer to Float64 data owned by the caller (station and cell inputs)."""

comptime F32Ptr = Pointer[Float32, MutUntrackedOrigin]
"""Pointer to Float32 data owned by the caller (the estimates)."""

comptime VECTOR_SIZE = simd_width_of[DType.float64]()
"""Number of Float64 values the CPU processes in one SIMD instruction."""


def untracked[T: AnyType](pointer: Pointer[T, _]) -> Pointer[T, MutUntrackedOrigin]:
    """Returns `pointer` without its origin, so it can be stored in a struct field.

    Only used for the arguments of the public kernel functions: the caller's
    pointer is in use for the whole call, so the memory behind it outlives every
    use inside the kernel.

    Parameters:
        T: Type of the values pointed to.

    Args:
        pointer: A pointer into memory the caller keeps alive for the whole call.

    Returns:
        A pointer to the same address with an untracked origin.
    """
    return Pointer[T, MutUntrackedOrigin](unsafe_from_address=Int(pointer))
