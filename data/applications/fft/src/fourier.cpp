
#include "fourier.hpp"
#include <cmath>
#include <fstream>
#include <map>
#include <approx.h>

void calcFftIndices(int K, int* indices)
{
	int i, j ;
	int N ;

	N = (int)log2f(K) ;

	indices[0] = 0 ;
	indices[1 << 0] = 1 << (N - (0 + 1)) ;
	for (i = 1; i < N; ++i)
	{
		for(j = (1 << i) ; j < (1 << (i + 1)); ++j)
		{
			indices[j] = indices[j - (1 << i)] + (1 << (N - (i + 1))) ;
		}
	}
}

void radix2DitCooleyTykeyFft(int K, int* indices, Complex* x, Complex* f)
{

	calcFftIndices(K, indices) ;

	int step ;
	float arg ;
	int eI ;
	int oI ;

	float fftSin;
	float fftCos;

	Complex t;
	int i ;
	int N ;
	int j ;
	int k ;

	double dataIn[1];
	double dataOut[2];

	for(i = 0, N = 1 << (i + 1); N <= K ; i++, N = 1 << (i + 1))
	{
		for(j = 0 ; j < K ; j += N)
		{
			step = N >> 1 ;
			for(k = 0; k < step ; k++)
			{
				arg = (float)k / N ;
				eI = j + k ; 
				oI = j + step + k ;

				dataIn[0] = arg;

#pragma parrot(input, "fft", [1]dataIn)

				fftSinCos(arg, &fftSin, &fftCos);

				dataOut[0] = fftSin;
				dataOut[1] = fftCos;

#pragma parrot(output, "fft", [2]<0.0; 2.0>dataOut)

				fftSin = dataOut[0];
				fftCos = dataOut[1];


				// Non-approximate
				t =  x[indices[eI]] ;
                Complex oI_val = x[indices[oI]];
                
                // Cálculos para parte real (produtos)
			 //anotacao:
                float real_cos_product = oI_val.real * fftCos;
			 //anotacao:
                float imag_sin_product = oI_val.imag * fftSin;
                
                // Cálculos para parte imaginária (produtos)
                float imag_cos_product = oI_val.imag * fftCos;
                float real_sin_product = oI_val.real * fftSin;
                
                // Termo comum em cálculos real
                float real_term = real_cos_product - imag_sin_product;
                
                // Termo comum em cálculos imaginários
                float imag_term = imag_cos_product + real_sin_product;
                
                // Cálculo final para eI.real
                x[indices[eI]].real = t.real + real_term;
                
                // Cálculo final para eI.imag
                x[indices[eI]].imag = t.imag + imag_term;
                
                // Cálculo final para oI.real
                x[indices[oI]].real = t.real - real_term;
                
                // Cálculo final para oI.imag
                x[indices[oI]].imag = t.imag - imag_term;





			}
		}
	}

	for (int i = 0 ; i < K ; i++)
	{
		f[i] = x[indices[i]] ;
	}
}