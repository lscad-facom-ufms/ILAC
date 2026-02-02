/*
 * kinematics.cpp
 * * Created on: Sep. 10 2013
 * Author: Amir Yazdanbakhsh <yazdanbakhsh@wisc.edu>
 * Refactored for single operation per line with annotations
 */

 #include <cmath>
 #include "kinematics.hpp"
 #include "approx.h"
 
 // Comprimentos dos elos do braço robótico
 float l1 = 0.5;
 float l2 = 0.5;
 
 // Função de cinemática direta
 void forwardk2j(float theta1, float theta2, float* x, float* y) {
      // Decomposição para term1
      float cos_theta1 = cos(theta1);
      
          
      float term1 = l1 * cos_theta1;

      // Decomposição para term2
      //anotacao:
      float theta_sum = theta1 + theta2;
      float cos_theta_sum = cos(theta_sum);
      
      //anotacao:
      float term2 = l2 * cos_theta_sum;

      // Cálculo final de X
      //anotacao:
      *x = term1 + term2;

      // Decomposição para term3
      float sin_theta1 = sin(theta1);
      
      //anotacao:
      float term3 = l1 * sin_theta1;

      // Decomposição para term4
      // Recalculando soma para manter atomicidade por linha
      //anotacao:
      float theta_sum_y = theta1 + theta2;
      float sin_theta_sum = sin(theta_sum_y);
      
      //anotacao:
      float term4 = l2 * sin_theta_sum;

      // Cálculo final de Y
      //anotacao:
      *y = term3 + term4;
 }
 
 // Função de cinemática inversa
 void inversek2j(float x, float y, float* theta1, float* theta2) {
      double dataIn[2];
      dataIn[0] = x;
      dataIn[1] = y;
 
      double dataOut[2];
 
 #pragma parrot(input, "inversek2j", [2]dataIn)
 
      // Quadrados básicos
      //anotacao:
      float x_squared = x * x;
      
      //anotacao:
      float y_squared = y * y;
      
      //anotacao:
      float l1_squared = l1 * l1;
      
      //anotacao:
      float l2_squared = l2 * l2;
      
      // Decomposição do numerator: x^2 + y^2 - l1^2 - l2^2
      //anotacao:
      float num_tmp1 = x_squared + y_squared;
      
      //anotacao:
      float num_tmp2 = num_tmp1 - l1_squared;
      
      //anotacao:
      float numerator = num_tmp2 - l2_squared;

      // Decomposição do denominator: 2 * l1 * l2
      //anotacao:
      float denom_tmp = l1 * l2;
      
      //anotacao:
      float denominator = 2.0 * denom_tmp;

      // Cálculo de theta2: acos(num / den)
      // Divisão não solicitada na lista (+,-,*), sem anotação
      float div_theta2 = numerator / denominator;
      *theta2 = (float)acos(div_theta2);

      // Pré-cálculos trigonométricos para theta2
      float cos_theta2 = cos(*theta2);
      float sin_theta2 = sin(*theta2);

      // term1 = l2 * cos(theta2)
      //anotacao:
      float term1 = l2 * cos_theta2;

      // term2 = l1 + term1
      //anotacao:
      float term2 = l1 + term1;

      // term3 = y * term2
      //anotacao:
      float term3 = y * term2;

      // term4 = x * l2
      //anotacao:
      float term4 = x * l2;

      // term5 = term4 * sin(theta2)
      //anotacao:
      float term5 = term4 * sin_theta2;

      // denominator2 = x^2 + y^2
      //anotacao:
      float denominator2 = x_squared + y_squared;

      // Cálculo final de theta1: asin((term3 - term5) / denominator2)
      //anotacao:
      float num_theta1 = term3 - term5;
      
      // Divisão não solicitada na lista (+,-,*), sem anotação
      float div_theta1 = num_theta1 / denominator2;
      *theta1 = (float)asin(div_theta1);
 
      dataOut[0] = (*theta1);
      dataOut[1] = (*theta2);
 
 #pragma parrot(output, "inversek2j", [2]<0.0; 2.0>dataOut)
 
      *theta1 = dataOut[0];
      *theta2 = dataOut[1];
 }