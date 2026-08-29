#! /bin/bash

# download everything from textfile
wget --content-disposition --no-clobber -i fonts.txt


# unzip to own folder
for filename in $(ls *.zip); do

dirname="${filename%.zip}"

mkdir -p "$dirname"
unzip -o "$filename" -d "$dirname"

done

# remove zip files
rm *.zip
