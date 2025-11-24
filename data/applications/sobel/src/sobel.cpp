#include "rgb_image.hpp"
#include "convolution.hpp"
#include <iostream>
#include <cmath>
#include <memory>  // Para std::shared_ptr

int main(int argc, const char* argv[]) {
    int x, y;
    float s = 0;

    // Usa std::shared_ptr em vez de boost::shared_ptr
    std::shared_ptr<Image> srcImagePtr(new Image());
    std::shared_ptr<Image> dstImagePtr(new Image());

    float w[][3] =  {{0, 0, 0},
                    {0, 0, 0},
                    {0, 0, 0}
                    };

    srcImagePtr->loadRgbImage(argv[1]);
    dstImagePtr->loadRgbImage(argv[1]);
    srcImagePtr->makeGrayscale();


    y = 0 ;
	
	// Start performing Sobel operation
	for( x = 0 ; x < srcImagePtr->width ; x++ ) {
		HALF_WINDOW(srcImagePtr, x, y, w) ;


			s = sobel(w);


		dstImagePtr->pixels[y][x]->r = s ;
		dstImagePtr->pixels[y][x]->g = s ;
		dstImagePtr->pixels[y][x]->b = s ;
	}

	for (y = 1 ; y < (srcImagePtr->height - 1) ; y++) {
		x = 0 ;
		HALF_WINDOW(srcImagePtr, x, y, w);

			s = sobel(w);

	
		dstImagePtr->pixels[y][x]->r = s ;
		dstImagePtr->pixels[y][x]->g = s ;
		dstImagePtr->pixels[y][x]->b = s ;


		for( x = 1 ; x < srcImagePtr->width - 1 ; x++ ) {
			WINDOW(srcImagePtr, x, y, w) ;
				
				s = sobel(w);

			dstImagePtr->pixels[y][x]->r = s ;
			dstImagePtr->pixels[y][x]->g = s ;
			dstImagePtr->pixels[y][x]->b = s ;

		}

		x = srcImagePtr->width - 1 ;
		HALF_WINDOW(srcImagePtr, x, y, w) ;
		

			s = sobel(w);

		dstImagePtr->pixels[y][x]->r = s ;
		dstImagePtr->pixels[y][x]->g = s ;
		dstImagePtr->pixels[y][x]->b = s ;
	}

	y = srcImagePtr->height - 1;

	for(x = 0 ; x < srcImagePtr->width ; x++) {
		HALF_WINDOW(srcImagePtr, x, y, w) ;
		
			s = sobel(w);

		dstImagePtr->pixels[y][x]->r = s ;
		dstImagePtr->pixels[y][x]->g = s ;
		dstImagePtr->pixels[y][x]->b = s ;

	}

    dstImagePtr->saveRgbImage(argv[2], std::sqrt(256 * 256 + 256 * 256));
    return 0;
}