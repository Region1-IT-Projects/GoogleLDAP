#!/usr/bin/python3
# A script to generate hashes of files
import os
import hashlib
dirs = []
inp = input("Enter name(s) of folder(s) (space separated) to hash [all]: ")
if len(inp) == 0:
    for i in os.scandir():
        if i.is_dir() and i.name[0] != '.':
            dirs.append(i.name)
else:
    dirs = inp.split()
for d in dirs:
    wd = os.getcwd() + '/' + d
    print("Proccessing files in",d)
    files = os.scandir(wd)
    brown = open(wd+"/hashes",'w')
    for f in files:
        if f.is_file() and f.name != "hashes":
            md5digest = hashlib.md5(open(f,'rb').read()).hexdigest()
            print("File {} has hash {}".format(f.name, md5digest))
            brown.write(f.name + '\t' + md5digest + '\n')
    brown.flush()
    brown.close()
print("done!")


    
