#!/usr/bin/python3
import os
import time
print("\n\n\n")
termwidth = os.get_terminal_size()[0]
print("FileVault Helper Utility".center(termwidth,'='))
print("\n")
adminUser = os.getlogin()
print("enter a username or list of usernames (space separated) that should be able to unlock FileVault.")
print("Please note that these user will be logging in via {}'s token.".format(adminUser))
users = input("> ").split()
adminPass = input("\n Enter password for {}: ".format(adminUser))
processed = 0

#sanitize password for bash use
for i in ["#","$","<",">","|"]
    adminPass = adminPass.replace(i, "\\"+i)

for u in users:
    processed += 1
    complete = (processed/len(users)
    print("\nProgress:\n["+round(processed*(termwidth-2))*"#"+"]")
    cmd = os.system('sudo /System/Library/CoreServices/ManagedClient.app/Contents/Resources/createmobileaccount -n {} -v -a {} -U {}'.format(u, adminUser, adminPass))

print("Done! Rebooting...")
time.sleep(2)
os.system("reboot")
