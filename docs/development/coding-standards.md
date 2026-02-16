# Coding Standards

## Naming Conventions

Variables, classes, and functions are integral building blocks of computer code and need to be named. Adopting clear and consistent naming conventions is essential for readable and maintainable code. These practices enhance collaboration by making it easier for coders to understand and navigate the codebase. By reducing ambiguity, naming conventions help prevent errors and improve overall code quality. In this section you will find clear guidelines for naming variables, classes and functions.

The goal of this standard is to reduce the need for extra documentation. In the best case, the code is so clear and consistent that it is understandable without any additional information and comments.

### Checklist

#### AC1+

* [ ] Names are *self-descriptive* (first priority) and *pronounceable* (second). If possible, names are *brief* (least important).
* [ ] Only English language is used; the grammatically meaningful order of words is used.
* [ ] Word separation and capitalization is *consistent* and follows the [language's standard](#language-standards) (e.g. snake_case/underscore, camelCase, etc.).
* [ ] Abbreviations are only used if they are consensus in the scientific community and unambiguous.
* [ ] The following should be avoided in names: numbers (e.g. `computeSize2()`), suffixes without scientific meaning (e.g. `computeSizeNew()`), and references to developers (e.g. `computeSizeMaxMustermann()`).

#### AC2+

* [ ] For languages without access modifiers (public, private, ..., e.g. NetLogo, Python or Go), use naming conventions to indicate access.

#### AC3

* [x] The project contains a file / webpage specifing the internal standards.
* [x] The {{% glossary ci-cd CI %}} is set up for {{% glossary formatter formatting %}} and {{% glossary linter linting %}} (incl. for naming conventions where possible).

### Language standards
* Python: Python Style guide PEP 08


------------

## Formatting

Code formatting is the process of adjusting the visual representation of the code, e.g. indentation, use of parentheses, spacing, etc. Consistently formatted code improves readability for other developers and also for your future self. Hence, good formatting prevents errors or makes it easier to find them. Furthermore, well-formatted code promotes faster development, as it is (1) easier to extend understandable code, and (2) it is always clear how new code should be formatted.

### Checklist

#### AC1+

* [ ] The code adhers to the language's formatting standards.
* [ ] If possible, the {{% glossary IDE "devolopment environment" %}} is set up to check the formatting during the input and to run the formatter on saving.
* [ ] Word separation and capitalization (snake_case/underscore, camelCase, ...).
* [ ] Indentation and line breaks. In particular, indentation is used, and line widths are limited.
* [ ] Spacing around operators and in braces/brackets (e.g. `=`, `+`, `array[i, j]`).

#### AC2+

See AC1+.

#### AC3+

* [x] The applied formatting conventions are specified somewhere in the project documentation / README.md / wiki.
* [x] The {{% glossary ci-cd CI %}} is set up to check the formatting on every push / Merge Request. The CI prevents merges if formatting warnings are encountered.
* [x] The CI is set up to run a {{% glossary linter %}} (the language's default linter if applicable). The {{% glossary ci-cd CI %}} prevents merges if formatting warnings are encountered.

### Language standards

---------------

## Code Structure

When a software project grows more complex, a proper structure of the code is of paramount importance for maintainability and flexibility for future extensions.
Code structure here refers to the use of functions, methods, objects, classes etc. to structure code, and to manage complexity and interactions.
This section covers the basics of structuring code and is not to be seen as an advanced topic dealing with specific designs and archtiectural patterns.

For simplicity, we use the term “function” as a synonym for “method” (i.e. a function belonging to an object).

### Checklist

#### AC1+

* [ ] The code is structured into functions and classes / structs. That is, there is no top-level code except in the `main` function / section, which *summarizes* what the program does when executed (the `main` method does not contain advanced program logic).
* [ ] Each function serves a single task. If a function executes multiple tasks, these tasks are performed in seperate (sub)-functions, forming a call hierarchy.
* [ ] Code repetition is avoided (where applicable by writing reusable functions or objects.)

#### AC2+

* [ ] There are no non-constant global variables. This applies in particular to data frames. Constants are only things that cannot possibly change (e.g. pi, conversion factors).
* [ ] Objects / structs are used for structuring data and functions. For example, all *model-wide* used variables are stored as attributes of a model object.
* [ ] Dependent or derived variables are avoided where possible so that redundancies and inconsistent states are prevented. Otherwise, these variables are "hidden" from the outside and only changeable via functions that alter all variables in a consistent way.

##### Non-trivial models and other structured software

* [ ] Submodels and subcontexts can be interchanged and therefore follow structural guidelines (?).
* [ ] {{% glossary state-variable "Variables" %}} and {{% glossary parameter parameters %}} that are only used internally in a certain subcontext (e.g. submodel) are stored in an object / {{% glossary struct struct %}} representing this subcontext.
* [ ] Functionality that operates on a certain subcontext is written as methods belonging to the corresponding object.
* [ ] {{% glossary entity Entities %}} and their {{% glossary state-variable "state variables" %}} are implemented as "array of structs" (i.e. "array of entities"), or using an Entity Component System ({{% glossary ecs %}}) architecture.
* [ ] Sets of self-contained classes, functions, etc. that may be usefully applied for themselves are structured into separate files / packages. 
* [ ] In languages that support packaging, the self-contained packages are separately installable or manageable using a standard build tool.

##### Analysis scripts

The items of AC1+ as well as the general items of AC2+ apply.

#### AC3

##### Models

* [ ] Functions that extract data from a model are implemented in a generic way / against an {{% glossary interface interface %}}. Any number of observers can be added to the model ([observer pattern](https://en.wikipedia.org/wiki/Observer_pattern)).
* [ ] Output data are stored independently from the model (e.g. in the observers).

##### Libraries

* [ ] There is a clearly specified set of objects, functions, variables, etc. that are exposed to the user.
* [ ] Only the necessary {{% glossary api %}} is exposed.

### Details

Observers:

-------------

## Documentation

To correctly apply, extend, or correct code, it needs to be understood. Though good code should be written so that it is easy to understand without further information, good code and software documentation helps to bridge knowledge gaps (e.g. the motivation behind a certain code section) and summarizes information so that working with the code becomes quicker and possible even for users who are not developers of the software. This lowers the barrier of maintaining and correcting code in the long run and is thus extremely important for creating software that is mostly error-free.

Note that after a while, you yourself may forget your own intentions when writing certain lines. Therefore, your may be the person who profits from your documentation most.

Documentation includes source code documentation itself as well as all additional documentation files, providing information e.g., on how to build, install and use your software.

### Checklist

#### AC1+

* [ ] The code is written so that it is self-explaining, thereby minimizing the need for comments.
* [ ] Every line that contains code that is not trivial / self-explaining is explained in an in-line comment.
* [ ] There are no inline comments that only contain information that can be trivially obtained from the code.
* [ ] Documentation and inline comments follow the language-specific conventions.
* [ ] The file names indicate what is done in the script.
* [ ] All documentation is written either in the language's documentation standard, or a markup language like Markdown or reStructuredText.
* [ ] Word and PDF files are not used for documentation.
* [ ] All documentation resides in the project's Git {{% glossary repository repository %}} (see [Version Control](./version_control.md)).

#### AC2+

##### Readme

* [ ] The project has a `README.md` file briefly explaining the purpose. If it is a one-file project, this information can be provided in the first lines of the script.
* [ ] The README enables people who don't know the project to build and run it.
* [ ] The README is clearly structured.
* [ ] If the code is or becomes publicly accessible, the project contains a license file and, if applicable, instructions on how to cite it and how to report bugs.
* [ ] When the code is archived or becomes publicly accessible, the README or another suited file contains full information on the versions of the packages / libraries the software depends on.

##### Docstrings

* [ ] Functions or methods that (1) do more than a single thing or (2) contain not self-explaining arguments or return values are documented with a docstring.
* [ ] Every class or {{% glossary struct struct %}} is documented with a docstring indicating its purpose.

#### AC3

##### Docstrings

* [ ] *Every* function or method is documented with a docstring that explains (1) what the function does, (2) all arguments that are not totally self-explaining, (3) what the function returns, (4) which errors are thrown/returned.
* [ ] The {{% glossary api %}} documentation is automatically extracted and published as website.

##### Documentation of documentation (meta-documentation)

* [x] The project contains a document with naming conventions and style guide. In case style is enforced by the language, the used tools are documented.
* [ ] The project uses {{% glossary git-tag "Git tags" %}} to indicate versions.
* [ ] The {{% glossary git-tag "Git tags" %}} follow semantic versioning.
* [x] The project contains a changelog, tracking significant changes between versions.

### Language standards

-----------

## Version Control

Version control software (VCS) enables you to track code changes and write code collaboratively.
Hence, version control can be helpful to find bugs by tracking down when certain lines of code were introduced.
Furthermore, as it preserves and documents the history of a software project, version control enables you to freely experiment with your code and to publish / reference specific versions of your code in publications.

The standard VCS at the OESA is Git, and we specifically refer to this software here. See why and under which circumstances you may deviate from this standard.

### Checklist

#### AC1+

Everything written under AC2+ is desirable and strongly recommended for AC1 as well. To simplify the setup, it is possible to use a purely local repositoryA software repository is an online storage to/from which code and other data can be up-/downloaded.
{{% glossary git %}} repositories offer the additional functionality to share and manage different versions of code and hence allow collaborative software development.
↪ Wikipedia
.

#### AC2+

* [x] The project is tracked with Git.
* [ ] The project has a remote {{% glossary repository repository %}}, preferably on UFZ GitLab.
* [ ] Commits are done frequently and typically focus on a single topic each.
* [ ] Contributors use meaningful commit messages.
* [ ] The commit messages start with a headline summarizing what is done in the commit.
* [ ] The commit messages focus on justifying what is done rather than just describing it.
* [ ] The commit messages are written in English and present tense.
* [ ] There is a `.gitignore` file excluding automatically generated files, such as the compiled program and intermediates. If possible, entire folders are ignored rather than individual files.

#### AC3

* [ ] Contributors use branches to work on the code.
* [ ] Contributions to the main branch are done via Merge Requests (aka Pull Requests) only.
* [ ] The main branch is protected from direct commits / pushes.
* [ ] There is a {{% glossary ci-cd CI %}} set up, testing commits prior to merging them to the main branch.

### Is Git the only permitted VCS?

Our standard VCS is Git, although there are alternatives like Mercurial or SVN.

Git is by far the most widely used VCS. This makes it easier to get help from colleagues and more likely that your collaborators are already familiar with it. Further, the UFZ hosts an instance of GitLab, on git.ufz.de. Also, typically training sessions for Git are offered at the UFZ on a regular basis.

Using other VCS is permitted in principle if (1) all project partners are familiar with the other VCS and (2) the other VCS entails operational advantages, e.g. better suited or already implemented for the specific project. Prior to deciding for using a different VCS, the advantages and disadvantages should be discussed with colleagues who are well informed about the various features of Git and GitLab, such as continuous integration, which may be needed for compliance with the RSE community guidelines.

---------

## Testing & Testability

Testing code is mandatory when working scientifically. The goal is to ensure the intented functioning of a program by employing a range of testing methods, such as unit testsUnit testing is a bottom-up testing approach, where small, isolated parts of the code are tested to validate the expected outcome.
Usually, all unit tests are run by a {{% glossary ci-cd %}} pipeline on every modification of the code base.
, visual debugging and continuous integrationContinuous integration / continuous deployment (CI/CD) is a service provided by Git forges to run things like tests on the server when changes are pushed or Merge Requests are created. ↪ Wikipedia
. To apply these testing methods, code needs to be structured in a testable way, which can be achieved by implementing the following these standards.

### Checklist

#### AC1+

No requirements.

#### AC2+

* [ ] Code is structured so that the "main" code only calls functions.
* [ ] Functions can be called without context such as non-constant globals.
* [ ] Functions do not modify global variables.
* [ ] All functions return the expected result for typical use cases and raise errors otherwise.
* [ ] There is a script, function, or test suite that can be run with a single command to verify that all functions run without errors.
* [ ] There is a visualization that can be used for *visual debugging*, i.e. to verify model behavior. For spatial models, this is a spatial visualization.
* [ ] Model fitting procedures are successfully tested with simulated (artificial) data.
* [ ] Stochastic models have the option to set the random seed to achieve reproducable simulation runs. Preferrably, the seed is specified at a (prominent) single place.

#### AC3+

* [ ] Model logic is covered by {{% glossary unit-testing "unit tests" %}} (not necessary for analysis / UI).
* [x] There is a {{% glossary ci-cd "CI workflow" %}} that runs on each push / Merge Request.
* [ ] The {{% glossary ci-cd CI %}} tests that the project compiles (for compiled languages).
* [x] The {{% glossary ci-cd CI %}} runs the {{% glossary unit-testing "unit tests" %}}.


--------------

## Interoperability

Interoperability between software covers all aspects of data and information exchange between different software. This includes import / export of data via standardized open file formats, simple communication between software, or the provision of an API [GLOSSARY].

Following these standards will help you to write interoperable software, especially if the integration in frameworks like UFZ’s model coupling framework FINAM is planned.

### Checklist

#### AC1+

There are no special requirements.

#### AC2+



* [ ] Where possible, only standardized open file formats are used. Formats can be domain-specific.
* [ ] If the software is implemented in a language that supports package / dependency managers, the software is bundled into a package. Otherwise, installation is made easy and well documented.



##### Models

* [ ] Model initialization is done in a separate function.
* [ ] Running the model for one time step is done in a dedicated function. (For models without time, this applies to the step where the results are computed.)
* [ ] Passing data (input) to the model is separated from reading / generating the input.
* [ ] Extracting data (output) from the model is separated from writing / processing the output.

#### AC3

* [x] The software can be compiled to a library / package (not only to an executable).
* [x] There is a well-documented {{% glossary api %}} so that the software can be integrated into other software.
* [ ] Providing a well-documented interface to Python or R has been seriously considered.
* [ ] Wrapping the software into a Python component for [FINAM](https://finam.pages.ufz.de/) has been seriously considered.
* [ ] The software is available publicly, if applicable in a way that package / dependency managers can handle it. If possible, the language's standard package index is used (e.g., PyPI, CRAN, crates.io).
* [x] The software uses [semantic versioning](https://semver.org/); the installed version is easily accessible by end users.
* [ ] The documentation is publicly available via a website.
