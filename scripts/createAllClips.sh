#!/usr/bin/env bash


pushd ../videoClips > /dev/null

for filename in $(ls *.json); do

  # abort if generate=false, grep returns 0 if no lines were found
  egrep "\"generateClip\": false," $filename
  if [ $? == 0 ] ;
  then
    echo "Do not generate";
    continue
  fi

  # change __NAME_OF_OUTPUT__ in myConfig.json to myConfig (video output = config name)
  filenameNoExtension=$(basename $filename .json)
  echo datei $filename - $filenameNoExtension

  # only if string is still present in file
  if grep "__NAME_OF_OUTPUT__" $filename; then
    sed -i "s/__NAME_OF_OUTPUT__/$filenameNoExtension/g" $filename
  fi

  # File is named like this '00002_20260823_164834.json'. Get the timestamp
  timestamp="${filename#*_}"
  timestamp="${timestamp%.json}"

  # insert timestamp in file as input file name
  if grep "__NAME_OF_INPUT__" $filename; then
    sed -i "s/__NAME_OF_INPUT__/$timestamp/g" $filename
  fi


  python ../scripts/clipexport.py $filename

  sed -i "s/^\(.*\"generateClip\":.*\)true,$/\1false,/" $filename
done


popd > /dev/null
