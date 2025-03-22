#!/usr/bin/env python3
import os
import json
import argparse

def find_compile_commands_files(directory):
    compile_commands_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.startswith("compile_commands_") and file.endswith(".json"):
                compile_commands_files.append(os.path.join(root, file))
    return compile_commands_files

def generate_testsuite_database(directory, output_file):
    compile_commands_files = find_compile_commands_files(directory)
    database = {}

    for file_path in compile_commands_files:
        with open(file_path, 'r') as file:
            data = json.load(file)
            testsuite_id = data.get("testsuite_id")
            platform = data.get("platform")
            files = data.get("files", [])

            for file in files:
                if file not in database:
                    database[file] = []
                database[file].append({"testsuite_id": testsuite_id, "platform": platform})

    with open(output_file, 'w') as output:
        json.dump(database, output, indent=4)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a testsuite database from compile_commands files.")
    parser.add_argument("--directory", required=True, help="Directory to search for compile_commands files")
    parser.add_argument("--output", required=True, help="Output file for the generated database")
    args = parser.parse_args()

    generate_testsuite_database(args.directory, args.output)
    print(f"Database generated and saved to {args.output}")