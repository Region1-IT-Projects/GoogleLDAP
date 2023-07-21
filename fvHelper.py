#!/usr/bin/python3
import os
print("\n\n\n")
termwidth = os.get_terminal_size()[0]
print("FileVault Helper Utility".center(termwidth,'='))
print("\n")
adminUser = os.getlogin()
print("enter a username or list of usernames (space separated) that should be able to unlock FileVault.")
print("Please note that these user will be logging in via {}'s token.".format(adminUser))
users = input("> ").split()
adminPass = input("\n Enter password for {}: ".format(adminUser))

#sanitize password for bash use
for i in ["#","$","<",">","|","*"]:
    adminPass = adminPass.replace(i, "\\"+i)

for u in users:
    #sanitize email address
    u = u.split("@")[0]
    os.system('sudo /System/Library/CoreServices/ManagedClient.app/Contents/Resources/createmobileaccount -n {} -v -a {} -U {}'.format(u, adminUser, adminPass))

print("Done!")
