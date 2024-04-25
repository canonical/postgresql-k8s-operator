#!/bin/bash

# Create an orphan branch
git switch --orphan charm-review-base

# Add a random file and commit it
echo "Random content" > random_file.txt
git add random_file.txt
git commit -m "Add random file"

# Remove the same file in subsequent commit
git rm random_file.txt
git commit -m "Remove random file"

# Push the branch to remote origin GitHub
git push origin charm-review-base

# Create a new branch with charm-review-base as the base
git checkout -b charm-review charm-review-base

# Copy files from main without commit history
git checkout main -- .

# Commit and push the files
git add .
git commit -m "Copy files from main"
git push origin charm-review