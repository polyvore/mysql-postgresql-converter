#!/usr/bin/python

"""
Fixes a MySQL dump made with the right format so it can be directly
imported to a new PostgreSQL database.

Dump using:
mysqldump --compatible=postgresql --default-character-set=utf8 -r databasename.mysql -u root databasename
"""

import re
import sys
import os
import time
import subprocess


def parse(input_filename, output_filename):
    "Feed it a file, and it'll output a fixed one"
    #add list for dist key and primary key


    # State storage
    if input_filename == "-":
        num_lines = -1
    else:
        num_lines = int(subprocess.check_output(["wc", "-l", input_filename]).strip().split()[0])
    tables = {}
    current_table = None
    creation_lines = []
    enum_types = []
    foreign_key_lines = []
    fulltext_key_lines = []
    sequence_lines = []
    cast_lines = []
    num_inserts = 0
    started = time.time()
    #add list for dist key and primary key
    dist_keys = []
    sort_keys = set()
    is_unsigned = True
    # Open output file and write header. Logging file handle will be stdout
    # unless we're writing output to stdout, in which case NO PROGRESS FOR YOU.
    if output_filename == "-":
        output = sys.stdout
        logging = open(os.devnull, "w")
    else:
        output = open(output_filename, "w")
        logging = sys.stdout

    if input_filename == "-":
        input_fh = sys.stdin
    else:
        input_fh = open(input_filename)

    for i, line in enumerate(input_fh):
        time_taken = time.time() - started
        percentage_done = (i+1) / float(num_lines)
        secs_left = (time_taken / percentage_done) - time_taken
        logging.write("\rLine %i (of %s: %.2f%%) [%s tables] [%s inserts] [ETA: %i min %i sec]" % (
            i + 1,
            num_lines,
            ((i+1)/float(num_lines))*100,
            len(tables),
            num_inserts,
            secs_left // 60,
            secs_left % 60,
        ))
        logging.flush()
        line = line.decode("utf8").strip().replace(r"\\", "WUBWUBREALSLASHWUB").replace(r"\'", "''").replace("WUBWUBREALSLASHWUB", r"\\")
        # Ignore comment lines
        if line.startswith("--") or line.startswith("/*") or line.startswith("LOCK TABLES") or line.startswith("DROP TABLE") or line.startswith("UNLOCK TABLES") or not line:
            continue
        # Outside of anything handling
        if current_table is None:
            # Start of a table creation statement?
            if line.startswith("CREATE TABLE"):
                current_table = line.split('"')[1]
                tables[current_table] = {"columns": []}
                creation_lines = []
            # Inserting data into a table?
            elif line.startswith("INSERT INTO"):
                output.write(line.encode("utf8").replace("'0000-00-00 00:00:00'", "NULL") + "\n")
                num_inserts += 1
            # ???
            else:
                print "\n ! Unknown line in main body: %s" % line

        # Inside-create-statement handling
        else:
            # Is it a column?
            if line.startswith('"'):
                useless, name, definition = line.strip(",").split('"',2)
                try:
                    type, extra = definition.strip().split(" ", 1)

                    # This must be a tricky enum
                    if ')' in extra:
                        type, extra = definition.strip().split(")")

                except ValueError:
                    type = definition.strip()
                    extra = ""
                is_unsigned = "unsigned" in extra
                is_utf8mb4 = "utf8mb4" in extra
                extra = re.sub("CHARACTER SET [\w\d]+\s*", "", extra.replace("unsigned", ""))
                extra = re.sub("COLLATE [\w\d]+\s*", "", extra.replace("unsigned", ""))
                extra = extra.replace("signed","")

                # See if it needs type conversion
                final_type = None
                set_sequence = None
                if type.startswith("tinyint("):
                    type = "int4"
                    set_sequence = True
                    final_type = "boolean"
                elif type.startswith("int(") and is_unsigned:
                    type = "bigint"
                    set_sequence = True
                elif type.startswith("int("):
                    type = "integer"
                    set_sequence = True
                elif type.startswith("bigint("):
                    type = "bigint"
                    set_sequence = True
                elif type.startswith("mediumint("):
                    type = "integer"
                    set_sequence = True
                elif type== "text" or type == "longtext" or type == "mediumtext" or type == "tinytext" :
                    type = ""
                    extra =""
                elif type.startswith("varchar("):
                    size = int(type.split("(")[1].rstrip(")"))
                    if is_utf8mb4:
                        type = "varchar(%s)" % (size * 4)
                    else:
                        type = "varchar(%s)" % (size * 3)
                elif type.startswith("char("):
                    size = int(type.split("(")[1].rstrip(")"))
                    if is_utf8mb4:
                        type = "char(%s)" % (size * 4)
                    else:
                        type = "char(%s)" % (size * 3)
                elif type.startswith("smallint(") and is_unsigned:
                    type = "integer"
                    set_sequence = True
                elif type.startswith("smallint("):
                    type = "int2"
                    set_sequence = True
                elif type == "datetime":
                    type = "timestamp with time zone"
                elif type == "double":
                    type = "double precision"
                elif type == "blob":
                    type = ""
                    extra =""
                elif type == "timestamp":
                    extra = extra.replace(extra[extra.find(" DEFAULT"):], "")
                elif type.startswith("enum(") or type.startswith("set("):

                    types_str = type.split("(")[1].rstrip(")").rstrip('"')
                    types_arr = [type_str.strip('\'') for type_str in types_str.split(",")]

                    # Considered using values to make a name, but its dodgy
                    # enum_name = '_'.join(types_arr)
                    # enum_name = "{0}_{1}".format(current_table, name)
                    #
                    # if enum_name not in enum_types:
                    #     output.write("CREATE TYPE {0} AS ENUM ({1}); \n".format(enum_name, types_str));
                    #     enum_types.append(enum_name)
                    #
                    size = max([len(type) for type in types_arr])

                    type = "varchar(%s)" % (size * 3)

                if final_type:
                    cast_lines.append("ALTER TABLE \"%s\" ALTER COLUMN \"%s\" DROP DEFAULT, ALTER COLUMN \"%s\" TYPE %s USING CAST(\"%s\" as %s)" % (current_table, name, name, final_type, name, final_type))
                # ID fields need sequences [if they are integers?]
                if name == "id" and set_sequence is True:
                    sequence_lines.append("CREATE SEQUENCE %s_id_seq" % (current_table))
                    sequence_lines.append("SELECT setval('%s_id_seq', max(id)) FROM %s" % (current_table, current_table))
                    sequence_lines.append("ALTER TABLE \"%s\" ALTER COLUMN \"id\" SET DEFAULT nextval('%s_id_seq')" % (current_table, current_table))
                # Record it
                if type != "" and extra != "":
                    creation_lines.append('"%s" %s %s' % (name, type, extra))
                    tables[current_table]['columns'].append((name, type, extra))
            # Is it a constraint or something?
            elif line.startswith("PRIMARY KEY"):
                creation_lines.append(line.rstrip(","))
                dist_keys.append(line.split('"')[1])
            elif line.startswith("CONSTRAINT"):
                foreign_key_lines.append("ALTER TABLE \"%s\" ADD CONSTRAINT %s DEFERRABLE INITIALLY DEFERRED" % (current_table, line.split("CONSTRAINT")[1].strip().rstrip(",")))
                foreign_key_lines.append("CREATE INDEX ON \"%s\" %s" % (current_table, line.split("FOREIGN KEY")[1].split("REFERENCES")[0].strip().rstrip(",")))
            elif line.startswith("UNIQUE KEY"):
                sortkey_list = line.split("(")[1].rstrip(")")
                for sortkey in sortkey_list.split(","):
                    if sortkey not in sort_keys and sortkey:
                        sort_keys.add(sortkey.rstrip(")").rstrip(' '))
            elif line.startswith("FULLTEXT KEY"):

                fulltext_keys = " || ' ' || ".join( line.split('(')[-1].split(')')[0].replace('"', '').split(',') )
                fulltext_key_lines.append("CREATE INDEX ON %s USING gin(to_tsvector('english', %s))" % (current_table, fulltext_keys))

            elif line.startswith("KEY"):
                sortkey_list = line.split("(")[1].rstrip(")")
                for sortkey in sortkey_list.split(","):
                    if sortkey not in sort_keys and sortkey:
                        sort_keys.add(sortkey.rstrip(")").rstrip(' '))
            # Is it the end of the table?
            elif line == ");":
                output.write("CREATE TABLE mysql.%s(\n"  % current_table)
                for i, line in enumerate(creation_lines):
                    output.write("    %s%s\n" % (line, "," if i != (len(creation_lines) - 1) else ""))
                output.write(")")
                if len(dist_keys) > 0:
                    output.write("\nDISTKEY("+str(dist_keys[0])+")\n")
                if len(sort_keys) > 0:
                    #more than one sortkey
                    output.write("SORTKEY("+','.join(list(sort_keys))+")")
                output.write(';\n')
                output.write("GRANT SELECT ON TABLE mysql."+current_table+" TO PUBLIC;")
                current_table = None
            else:
                print "\n ! Unknown line inside table creation: %s" % line


    print ""


if __name__ == "__main__":
    parse(sys.argv[1], sys.argv[2])
