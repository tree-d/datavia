# Checklist for Code Review

## Naming Conventions

Variables, classes, and functions are integral building blocks of computer code and need to be named. Adopting clear and consistent naming conventions is essential for readable and maintainable code. These practices enhance collaboration by making it easier for coders to understand and navigate the codebase. By reducing ambiguity, naming conventions help prevent errors and improve overall code quality. In this section you will find clear guidelines for naming variables, classes and functions.

The goal of this standard is to reduce the need for extra documentation. In the best case, the code is so clear and consistent that it is understandable without any additional information and comments.


### AC1+

* [x] Names are *self-descriptive* (first priority) and *pronounceable* (second). If possible, names are *brief* (least important).
* [x] Only English language is used; the grammatically meaningful order of words is used.
* [x] Word separation and capitalization is *consistent* and follows the [language's standard](#language-standards) (e.g. snake_case/underscore, camelCase, etc.).
* [x] Abbreviations are only used if they are consensus in the scientific community and unambiguous.
* [x] The following should be avoided in names: numbers (e.g. `computeSize2()`), suffixes without scientific meaning (e.g. `computeSizeNew()`), and references to developers (e.g. `computeSizeMaxMustermann()`).

### AC2+

* [x] For languages without access modifiers (public, private, ..., e.g. NetLogo, Python or Go), use naming conventions to indicate access.

### AC3

* [ ] The project contains a file / webpage specifing the internal standards.
* [ ] The {{% glossary ci-cd CI %}} is set up for {{% glossary formatter formatting %}} and {{% glossary linter linting %}} (incl. for naming conventions where possible).