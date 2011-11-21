"""passlib._gen_salsa - meta script that generates _salsa.py"""
#==========================================================================
# imports
#==========================================================================
# core
import os
# pkg
# local
#==========================================================================
# constants
#==========================================================================

_SALSA_OPS = [
        # row = (target idx, source idx 1, source idx 2, rotate)
        # interpreted as salsa operation over uint32...
        #   target = (source1+source2)<<rotate

        ##/* Operate on columns. */
        ##define R(a,b) (((a) << (b)) | ((a) >> (32 - (b))))
        ##x[ 4] ^= R(x[ 0]+x[12], 7);  x[ 8] ^= R(x[ 4]+x[ 0], 9);
        ##x[12] ^= R(x[ 8]+x[ 4],13);  x[ 0] ^= R(x[12]+x[ 8],18);
        (  4,  0, 12,  7),
        (  8,  4,  0,  9),
        ( 12,  8,  4, 13),
        (  0, 12,  8, 18),

        ##x[ 9] ^= R(x[ 5]+x[ 1], 7);  x[13] ^= R(x[ 9]+x[ 5], 9);
        ##x[ 1] ^= R(x[13]+x[ 9],13);  x[ 5] ^= R(x[ 1]+x[13],18);
        (  9,  5,  1,  7),
        ( 13,  9,  5,  9),
        (  1, 13,  9, 13),
        (  5,  1, 13, 18),

        ##x[14] ^= R(x[10]+x[ 6], 7);  x[ 2] ^= R(x[14]+x[10], 9);
        ##x[ 6] ^= R(x[ 2]+x[14],13);  x[10] ^= R(x[ 6]+x[ 2],18);
        ( 14, 10,  6,  7),
        (  2, 14, 10,  9),
        (  6,  2, 14, 13),
        ( 10,  6,  2, 18),

        ##x[ 3] ^= R(x[15]+x[11], 7);  x[ 7] ^= R(x[ 3]+x[15], 9);
        ##x[11] ^= R(x[ 7]+x[ 3],13);  x[15] ^= R(x[11]+x[ 7],18);
        (  3, 15, 11,  7),
        (  7,  3, 15,  9),
        ( 11,  7,  3, 13),
        ( 15, 11,  7, 18),

        ##/* Operate on rows. */
        ##x[ 1] ^= R(x[ 0]+x[ 3], 7);  x[ 2] ^= R(x[ 1]+x[ 0], 9);
        ##x[ 3] ^= R(x[ 2]+x[ 1],13);  x[ 0] ^= R(x[ 3]+x[ 2],18);
        (  1,  0,  3,  7),
        (  2,  1,  0,  9),
        (  3,  2,  1, 13),
        (  0,  3,  2, 18),

        ##x[ 6] ^= R(x[ 5]+x[ 4], 7);  x[ 7] ^= R(x[ 6]+x[ 5], 9);
        ##x[ 4] ^= R(x[ 7]+x[ 6],13);  x[ 5] ^= R(x[ 4]+x[ 7],18);
        (  6,  5,  4,  7),
        (  7,  6,  5,  9),
        (  4,  7,  6, 13),
        (  5,  4,  7, 18),

        ##x[11] ^= R(x[10]+x[ 9], 7);  x[ 8] ^= R(x[11]+x[10], 9);
        ##x[ 9] ^= R(x[ 8]+x[11],13);  x[10] ^= R(x[ 9]+x[ 8],18);
        ( 11, 10,  9,  7),
        (  8, 11, 10,  9),
        (  9,  8, 11, 13),
        ( 10,  9,  8, 18),

        ##x[12] ^= R(x[15]+x[14], 7);  x[13] ^= R(x[12]+x[15], 9);
        ##x[14] ^= R(x[13]+x[12],13);  x[15] ^= R(x[14]+x[13],18);
        ( 12, 15, 14,  7),
        ( 13, 12, 15,  9),
        ( 14, 13, 12, 13),
        ( 15, 14, 13, 18),
]

def main():
    target = os.path.join(os.path.dirname(__file__), "_salsa.py")
    fh = file(target, "w")
    write = fh.write

    VNAMES = ["v%d" % i for i in range(16)]

    PAD = " " * 4
    PAD2 = " " * 8
    PAD3 = " " * 12
    TLIST = ", ".join("b%d" % i for i in range(16))
    VLIST = ", ".join(VNAMES)
    kwds = dict(
        VLIST=VLIST,
        TLIST=TLIST,
    )

    write("""\
\"""passlib.utils._salsa - salsa 20/8 core, autogenerated by _gen_salsa.py\"""
#=================================================================
# salsa function
#=================================================================

def salsa20(input):
    \"""apply the salsa20/8 core to the provided input

    :args input: input list containing 16 32-bit integers
    :returns: result list containing 16 32-bit integers
    \"""

    %(TLIST)s = input
    %(VLIST)s = \\
        %(TLIST)s

    i = 0
    while i < 4:
""" % kwds)

    for idx, (target, source1, source2, rotate) in enumerate(_SALSA_OPS):
        write("""\
        # salsa op %(idx)d: [%(it)d] ^= ([%(is1)d]+[%(is2)d])<<<%(rot1)d
        t = (%(src1)s + %(src2)s) & 0xffffffff
        %(dst)s ^= ((t & 0x%(rmask)08x) << %(rot1)d) | (t >> %(rot2)d)

""" % dict(
        idx=idx, is1 = source1, is2=source2, it=target,
        src1=VNAMES[source1],
        src2=VNAMES[source2],
        dst=VNAMES[target],
        rmask=(1<<(32-rotate))-1,
        rot1=rotate,
        rot2=32-rotate,
    ))

    write("""\
        i += 1

""")

    for idx in range(16):
        write(PAD + "b%d = (b%d + v%d) & 0xffffffff\n" % (idx,idx,idx))

    write("""\

    return %(TLIST)s

#=================================================================
# eof
#=================================================================
""" % kwds)
        
if __name__ == "__main__":
    main()

#==========================================================================
# eof
#==========================================================================