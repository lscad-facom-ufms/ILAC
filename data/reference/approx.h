#ifndef APPROX_H
#define APPROX_H

#pragma STDC FENV_ACCESS OFF

static inline __attribute__((always_inline)) float FADDX(float a, float b) {
    float res;
    asm volatile ("faddx.s %0, %1, %2" : "=f"(res) : "f"(a), "f"(b));
    return res;
}

static inline __attribute__((always_inline)) float FSUBX(float a, float b) {
    float res;
    asm volatile ("fsubx.s %0, %1, %2" : "=f"(res) : "f"(a), "f"(b));
    return res;
}

static inline __attribute__((always_inline)) float FMULX(float a, float b) {
    float res;
    asm volatile ("fmulx.s %0, %1, %2" : "=f"(res) : "f"(a), "f"(b));
    return res;
}

static inline __attribute__((always_inline)) int ADDX(int a, int b) {
    int res;
    asm volatile ("addx %0, %1, %2" : "=r"(res) : "r"(a), "r"(b));
    return res;
}

static inline __attribute__((always_inline)) int MULX(int a, int b) {
    int res;
    asm volatile ("mulx %0, %1, %2" : "=r"(res) : "r"(a), "r"(b));
    return res;
}

#endif // APPROX_H