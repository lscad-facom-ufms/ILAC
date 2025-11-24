#include "rgb_image.hpp"
#include <regex>  // Para split_regex substituído

void Image::printPixel(int x, int y) {
    std::cout << "# Red:   " << this->pixels[x][y]->r << std::endl;
    std::cout << "# Green: " << this->pixels[x][y]->g << std::endl;
    std::cout << "# Blue:  " << this->pixels[x][y]->b << std::endl; 
}

int Image::loadRgbImage(std::string filename) {
    std::ifstream imageFile;

    if (DEBUG)
        std::cout << "# Loading " << filename << " ..." << std::endl;

    imageFile.open(filename);
    if (!imageFile.is_open()) {
        std::cerr << "# Error opening the file!" << std::endl;
        return 0;
    }

    // Lê a primeira linha e divide usando regex padrão
    std::string line;
    std::getline(imageFile, line);
    std::regex re("\t*,\t*");
    std::vector<std::string> imageInfo(
        std::sregex_token_iterator(line.begin(), line.end(), re, -1),
        std::sregex_token_iterator()
    );
    this->width = std::stoi(imageInfo[0]);
    this->height = std::stoi(imageInfo[1]);

    if (DEBUG) {
        std::cout << "# Width:  " << this->width;
        std::cout << "# Height: " << this->height << std::endl;
    }

    // Processa cada linha da imagem
    for (int h = 0; h < this->height; h++) {
        std::getline(imageFile, line);
        std::vector<std::string> currRowString(
            std::sregex_token_iterator(line.begin(), line.end(), re, -1),
            std::sregex_token_iterator()
        );

        std::vector<std::shared_ptr<Pixel>> currRow;

        for (int w = 0; w < this->width; w++) {
            int index = w * 3;
            float r = std::stof(currRowString[index++]);
            float g = std::stof(currRowString[index++]);
            float b = std::stof(currRowString[index++]);
            currRow.push_back(std::make_shared<Pixel>(r, g, b)); // Usa std::make_shared
        }
        this->pixels.push_back(currRow);
    }

    std::getline(imageFile, line);
    this->meta = line;
    return 1;
}

int Image::saveRgbImage(std::string outFilename, float scale)
{
	if(DEBUG)
	{
		std::cout << "# Savint into " << outFilename << " ..." << std::endl ;
	}

	std::ofstream outFile ;
	outFile.open(outFilename.c_str()) ;

	outFile << this->width << "," << this->height << std::endl ;

	for(int h = 0 ; h < this->height ; h++)
	{
		for(int w = 0 ; w < (this->width - 1); w++)
		{
			// Write Red
			int red   = (int)(this->pixels[h][w]->r * scale) ;
			int green = (int)(this->pixels[h][w]->g * scale) ;
			int blue  = (int)(this->pixels[h][w]->b * scale) ;

			//if ( red > 255 )
		//		red = 255 ;
		//	if ( green > 255 )
		//		green = 255 ;
		///	if ( blue > 255 )
			//	blue = 255 ;
			outFile << red << "," ;
			// Write Green
			outFile << green << "," ;
			// Write Blue
			outFile << blue << "," ;
			
		}

		int red   = (int)(this->pixels[h][this->width - 1]->r * scale) ;
		int green = (int)(this->pixels[h][this->width - 1]->g * scale);
		int blue  = (int)(this->pixels[h][this->width - 1]->b * scale) ;


		// Write Red
		outFile << red  << "," ;
		// Write Green
		outFile << green << "," ;
		// Write Blue
		outFile << blue << std::endl ;
	}

	// Print the meta information
	outFile << this->meta ;
	outFile.close() ;
	return 1 ;
}

void Image::makeGrayscale()
{

	float luminance ;

	float rC = 0.30 / 256.0 ;
	float gC = 0.59 / 256.0 ;
	float bC = 0.11 / 256.0 ;

	for(int h = 0 ; h < this->height ; h++)
	{
		for(int w = 0 ; w < this->width ; w++)
		{
			luminance = ( rC * this->pixels[h][w]->r ) + 
						( gC * this->pixels[h][w]->g ) + 
						( bC * this->pixels[h][w]->b ) ;

			this->pixels[h][w]->r = luminance ;
			this->pixels[h][w]->g = luminance ; 
			this->pixels[h][w]->b = luminance ; 
		}
	}
}