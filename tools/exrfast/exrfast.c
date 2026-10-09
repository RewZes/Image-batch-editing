/* Undoes OpenEXR's ZIP/RLE predictor and byte interleaving in one pass (the slow part of reading a .cxr in
   Python). Build:  x86_64-w64-mingw32-gcc -O3 -shared -static-libgcc -o exrfast.dll exrfast.c
                    gcc -O3 -shared -fPIC -o exrfast.so exrfast.c                                          */
#include <stddef.h>
#include <stdint.h>
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

/* in: n predicted bytes as stored; out: n bytes of pixel data.
   t[0] = in[0]; t[i] = t[i-1] + in[i] - 128 (mod 256); out[2k] = t[k], out[2k+1] = t[half + k] */
EXPORT void exr_unpredict(const uint8_t *in, uint8_t *out, size_t n)
{
    if (n == 0) return;
    size_t half = (n + 1) / 2, i;
    uint8_t t = in[0];
    out[0] = t;
    for (i = 1; i < half; i++) {
        t = (uint8_t)(t + in[i] - 128);
        out[2 * i] = t;
    }
    for (i = half; i < n; i++) {
        t = (uint8_t)(t + in[i] - 128);
        out[2 * (i - half) + 1] = t;
    }
}
