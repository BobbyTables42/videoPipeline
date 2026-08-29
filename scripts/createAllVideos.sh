#!/usr/bin/env bash


pushd ../videoFinal > /dev/null

for filename in $(ls *.json); do

  # change __NAME_OF_OUTPUT__ in myConfig.json to myConfig (video output = config name)
  filenameNoExtension=$(basename $filename .json)
  echo datei $filename - $filenameNoExtension

  # only if string is still present in file
  if grep "__NAME_OF_OUTPUT__" $filename; then
    sed -i "s/__NAME_OF_OUTPUT__/$filenameNoExtension/g" $filename
  fi

  python ../scripts/reelcompile.py $filename
done


popd > /dev/null
