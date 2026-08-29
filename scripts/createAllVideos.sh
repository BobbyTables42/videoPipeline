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


  # find all "__CLIP_00042_CLIP__" in json file
  readarray -t filesArray < <(grep "__CLIP_" $filename)
  printf 'print %s\n' "${filesArray[@]}"


  for ((i = 0; i < ${#filesArray[@]}; i++))
  do

    line="${filesArray[$i]}"
    echo line $line

    # get id (00042 in this example)
    id=`echo $line | sed 's/^.*__CLIP_\(.*\)_CLIP__.*$/\1/'`
    echo id $id

    # get name of clip with id 42
    clipname=$(ls -1 ../videoClips/${id}_*.mp4)
    echo videodatei $clipname

    sed -i "s#__CLIP_${id}_CLIP__#${clipname}#g" $filename

  done

  python ../scripts/reelcompile.py $filename
done


popd > /dev/null
