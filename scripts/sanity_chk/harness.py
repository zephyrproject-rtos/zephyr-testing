import re


class Harness:
    def __init__(self):
        self.state = None
        self.type = None
        self.regex = []
        self.counter = []
        self.repeat = 1

    def configure(self, instance):
        config = instance.test.harness_config
        if "type" in config:
            self.type = config['type']
            self.regex = config['regex']
        if "repeat" in config:
            self.repeat = config['repeat']

class Console(Harness):

    def handle(self, line):
        if self.type == "one_line":
            pattern = re.compile(self.regex[0])
            if pattern.match(line):
                self.state = "passed"
        elif self.type == "multi_line":
            for r in self.regex:
                pattern = re.compile(r)
                if pattern.match(line) and not line in self.counter:
                    self.counter.append(line)

            if len(self.counter) == len(self.regex):
                self.state = "passed"



class Test(Harness):
    RUN_PASSED = "PROJECT EXECUTION SUCCESSFUL"
    RUN_FAILED = "PROJECT EXECUTION FAILED"

    def handle(self, line):
        if line == self.RUN_PASSED:
            self.state = "passed"

        if line == self.RUN_FAILED:
            self.state = "failed"
