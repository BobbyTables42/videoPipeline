#!/usr/bin/env bash


pushd ../videoClips > /dev/null

for filename in $(ls *.json); do

  # change __NAME_OF_CLIP__ in myConfig.json to myConfig (video output = config name)
  filenameNoExtension=$(basename $filename .json)
  echo datei $filename - $filenameNoExtension

  sed -i "s/__NAME_OF_CLIP__/$filenameNoExtension/g" $filename

  python ../scripts/clipexport.py $filename
done


popd > /dev/null
