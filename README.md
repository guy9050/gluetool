citool - Continues Integration Swiss Army Knife
-----------------------------------------------

Installation
------------
It is recommended to install the tool via citool repository. Currently latest Fedora and RHEL6+ repositories should be available:

  https://copr.devel.redhat.com/coprs/mvadkert/citool/

You can also install via setup.py by running:

  # python setup.py install

To verify that citool is installed and working run the command to print the version of the tool

  # citool -V


Invocation
----------
For invocation use the citool command line tool which is able to sequentially execute unlimited number of plugins.
The citool command is designed to to run multiple plugins on one command line. The synopsis of the command is:

 $ citool [citool_args] [plugin1] [plugin1_args, ...] [plugin2] [plugin2_args, ...]

To list the program help and list all the plugins with description run

 $ citool -h

You can get a detailed help for each plugin by supplying the -h option. For example to view the help of the rpmdiff plugin use:

 $ citool rpmdiff -h

The citool_args options are options for the citool itself. These are options that somehow influence all the plugins. This is for example option for enabling the verbose and debug mode, printing info about the command line (for easy replication of the citool command) and supressing the info messages.

The plugins are executed sequentially. Each plugin can share information only with plugins that are executed later. The information sharing is implemented via shared functions - see 'Architecture' section for more information. The information sharing between plugins enhances usability of the plugins. With it you do not need to pass the same information for each plugin. 


Configuration
-------------
The citool defaults can be configured via configuration files '~/.citool' and '/etc/citool'. The configuration file is parsed by the ConfigParser module's parser, and it has this semantics

[default]
option=value

The plugins configuration is store in ~/.

Architecture
------------
As mentioned before, libcitool consists of libci.py, plugins and the citool command line tool. The idea behind citool is to have as much as possible self-contained plugins that provide all the functionality. The core - libcitool.py - provides interfaces and convienence functions for citool commandline tool and the plugins. It also manages the plugins, that includes instantiation of plugin objects, configuration according to the given command line options and configuration file and is responsible for plugins execution.

The core - libci.py
-------------------
The libci.py itself is actually a simple plugin manager. It tries to be decoupled from the plugins as much as possible. The libcitool provides two classes:

Ci class
---------
The citool class implementes various functions for managing plugins, parsing the configuration file, shared functions functionality and other convienence tools.

  * Plugin management
    * Importing plugins - At Ci instance initialization all plugins from the plugins subfolder in the module directory are imported - see _load_plugins(self) function. At the import a dictionary self.plugins with information about class, description and specific group of all plugin is created. This is used for generation of plugins list. At import not plugins object instance is created.

    * Initialization of plugins objects - To initialize a new plugin object the class provides the method add_plugin_instance(self, plugin_name) which initializes a new plugin instance according to given plugin name and adds it to self.plugin_instances list. This list will contain all initialized plugin and can be iterated over.

    * Destroying of plugins - The plugins can define a destroy function which is intended to be run at the end of citool execution like a cleanup. The function destroy_plugins calls destroy functions of all initialised plugins.  

  * Configuration file parser - Ci initializes the configuration file parser (self.cparser) from file '~/.citool' which is used by a Plugins method to initialize plugins from the configuration file. See section "Configuration" for more information.

  * Shared functions - Shared functions provide a simple way of sharing data between plugins. Each plugin can share an unlimited amount of it's own functions for other plugins. Note that the shared functions dictionary key is the function name, thus the last executed plugin will overwrite references to previous shared functions of the same name. This is useful as the same data can be provided by multiple plugins and the other plugins consuming the data simply do not care which plugin made the data available.

  * Convience functions
    Like generating plugin list with descriptions for help or verbose, info or debug messages.

Plugin class
------------
Is the base class for all the plugins. Subclasses of this class are searched in the plugins directory and only files containing them are imported. Each plugin needs to define an unique subclass name. For detailed information about important python class variables and methods see the plugins in the example group. Some important notes:

   * Plugin name - The plugin name needs to be unique and should not collide with other plugin positional parameters (keyword argument do not cause a collision).

   * Plugins grouping - The plugins can be grouped. The groups are defined by the name of subdirectories in the plugins directory. To add a plugin to a group place the plugin file in the subdirectory.

   * Plugin options - Each plugin can define its options via the 'options' dictionary. This dictionary is then used by functions for parsing options from command line option, parsing options from configuration file or initialization via an init function. The parsed options are stored in self._config dictionary and can be accessed via 'self.option' function.

   * Calling shared functions - To call a shared function use the 'self.shared' function with the shared function name and optional parameters passed to the shared function.

* retry decorator
The retry decorator can be used to decorate a function and catch any given exception and raise libcitoolRetry exception instead. This then can be catched and retry all the plugins execution. The idea behind this functionality was to workaround issues with installation of various Beaker machines. If the installation would fail, the execution (i.e. running all plugins from scratch) would be retried a few items. The retry decorator needs at least one exception class name as a parameter. Multiple exceptions can be specified.

For example the below decorator specifies, that libciError exception will be catched and libcitoolRetryException will be raised instead.

@retry(AssertionError, libcitoolException)
def execute(self):
...

* libciError class
This is the exception that should be used by plugins to raise an error. The citool tool catches this exception and reports the error with the plugin name for quick pinpoint to the issue.

* libciRetryError class
This exception should be used to raise an exception that should cause all plugins reexecution from scratch. The developer can also take advantage of the retry decorator to "override" other exceptions in a function.

Various notes
-------------
The plugins need to have unique name and they also should not collide with any positional arguments (note that collision with keyword arguments will not cause collision).


