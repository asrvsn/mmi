#!/bin/sh
# Host checks for the firmware: the pure logic (logic_test), then the drive and rail code itself against a mocked
# Teensy, a simulated TMC2209 and simulated stages (sim_test). Run from anywhere; needs a C++17 compiler.
set -e
cd "$(dirname "$0")"
CXX=${CXX:-c++}
S=../stage
mkdir -p build
$CXX -std=c++17 -O1 -Wall -Wextra -Wno-unused-parameter -I$S -o build/logic_test logic_test.cpp \
  $S/text.cpp $S/yaml.cpp $S/params.cpp $S/motion.cpp
./build/logic_test
if [ -f sim_test.cpp ]; then
  $CXX -std=c++17 -O1 -Wall -Wno-unused-parameter -Wno-unused-function -Isim -I$S -o build/sim_test sim_test.cpp \
    sim/teensy_sim.cpp $S/text.cpp $S/params.cpp $S/motion.cpp $S/drive.cpp $S/rail.cpp $S/light.cpp $S/lid.cpp
  ./build/sim_test
fi
