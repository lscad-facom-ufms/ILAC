#ifndef __RGB_IMAGE_HPP__
#define __RGB_IMAGE_HPP__

#include <memory>       // Substitui boost/shared_ptr
#include <vector>
#include <fstream>
#include <string>
#include <regex>        // Substitui boost/algorithm/string/regex
#include <iostream>
#include <sstream>      // Para manipulação de strings

#define DEBUG 0

class Pixel {
public:
    Pixel(float r, float g, float b) : r(r), g(g), b(b) {}
    float r;
    float g;
    float b;
};

class Image {
public:
    int width;
    int height;
    std::vector<std::vector<std::shared_ptr<Pixel>>> pixels; // Usa std::shared_ptr
    std::string meta;

    Image() : width(0), height(0) {}

    int loadRgbImage(std::string filename);
    int saveRgbImage(std::string outFilename, float scale);
    void makeGrayscale();
    void printPixel(int x, int y);
};

#endif