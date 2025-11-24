#ifndef APPROX_H
#define APPROX_H

// Desativa o acesso ao ambiente de ponto flutuante para otimização de desempenho
#pragma STDC FENV_ACCESS OFF

// Função de subtração aproximada para float
inline float FSUBX(float i, float s) {
    float result;
    asm volatile (
        "fsubx.s %[z], %[x], %[y]\n\t"
        : [z] "=f" (result)
        : [x] "f" (i), [y] "f" (s)
    );
    return result;
}

// Função de divisão aproximada para float
inline float FDIVX(float i, float s) {
    float result;
    asm volatile (
        "fdivx.s %[z], %[x], %[y]\n\t"
        : [z] "=f" (result)
        : [x] "f" (i), [y] "f" (s)
    );
    return result;
}

// Função de adição aproximada para float
inline float FADDX(float i, float s) {
    float result;
    asm volatile (
        "faddx.s %[z], %[x], %[y]\n\t"
        : [z] "=f" (result)
        : [x] "f" (i), [y] "f" (s)
    );
    return result;
}

// Função de multiplicação aproximada para float
inline float FMULX(float i, float s) {
    float result;
    asm volatile (
        "fmulx.s %[z], %[x], %[y]\n\t"
        : [z] "=f" (result)
        : [x] "f" (i), [y] "f" (s)
    );
    return result;
}

// Função de adição aproximada para inteiros
inline int ADDX(int i, int s) {
    int result;
    asm volatile (
        "addx %[z], %[x], %[y]\n\t"
        : [z] "=r" (result)
        : [x] "r" (i), [y] "r" (s)
    );
    return result;
}

// Função de multiplicação aproximada para inteiros
inline int MULX(int i, int s) {
    int result;
    asm volatile (
        "mulx %[z], %[x], %[y]\n\t"
        : [z] "=r" (result)
        : [x] "r" (i), [y] "r" (s)
    );
    return result;
}

#endif // APPROX_H
