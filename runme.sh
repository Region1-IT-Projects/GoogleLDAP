echo "Getting ready to install Google LDAP."
echo "I'm going to need elevated privileges. Please enter your sudo password when prompted"
echo "\n"
echo "Downloading Python 3..."
curl -O https://www.python.org/ftp/python/3.11.4/python-3.11.4-macos11.pkg
echo "\n Done. Installing Python 3..."
sudo installer -pkg python-3.11.4-macos11.pkg -target /
echo "Installing libraries..."
pip3 install pyobjc-framework-OpenDirectory
if [ $? -ne 0 ] ; then
    echo "pip libary install failed, please try again manually"
    exit 1
fi
echo "Running python script"
sudo python3 main23.pyobjc
if [ $? -eq 127 ] ; then
    echo "looks like the python3 install failed"
    exit 1
elif [ $? -ne 0 ] ; then
    echo "Sorry, something went wrong with the python script :("
    exit 1
fi
# we don't need a success condition because if successful, the python script reboots the computer.
