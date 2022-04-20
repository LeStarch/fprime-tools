""" fprime.fbuild.target: build target support

Contains the supporting definitions for build targets. These targets are used to run various parts of the build and may
contain build system targest (e.g. CMake target invokers), and miscellaneous targets that perform other actions.

@author lestarch
"""
import functools
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import List, Set
from .types import BuildType, NoSuchTargetException


class TargetScope(Enum):
    """ Scoping for target execution: GLOBAL, LOCAL

    GLOBAL targets trigger top-level (global) build system targets. LOCAL targets trigger per-directory build system
    targets. BOTH represents a target that can operate in both LOCAL and GLOBAL mode one at a time. When registering
    a BOTH targets, the system will create a local target and a global target and register those. These targets differ
    in both scope and flags as the GLOBAL target receives the flag "--all" added to its list.
    """
    GLOBAL = 0x1
    LOCAL = 0x2
    BOTH = GLOBAL | LOCAL


class ExecutableAction(ABC):
    """ Executable action not declaring a formal mnemonic, description, etc.

    Some steps in the execution of a composite target need to execute "actions lite" or anonymous targets. Things that
    have an execute method but are only executable through other targets. This class can be derived to create that
    without generating all the normal target metadata.
    """
    @abstractmethod
    def execute(self, builder: "Builder", context: Path, args: dict):
        """ Executes the given target """


class Target(ExecutableAction):
    """Generic build target base class

    A target can be specified by the user using a mnemonic and flags. The mnemonic is the command typed in by the user,
    and the flags allow the user to remember fewer mnemonics by changing the build target using a modifier. Each build
    target is available in certain build types.

    Targets can be global, using the GlobalTarget base class. Global targets don't use contextual information to modify
    the target, but apply to the whole deployment. Note: global targets are also engaged at the deployment level should
    that be the context.

    Targets may also be local. These targets use context information to figure out what to build. This allows for one
    target to represent a class of targets. i.e. build can be used as a local target to build any given sub directory.
    """
    ALL_TARGETS = []

    def __init__(
        self,
        mnemonic: str,
        desc: str,
        scope: TargetScope,
        build_type: BuildType = None,
        flags: set = None,
    ):
        """Constructs a build target and registers it as one of the global targets

        As part of the construction of a Target it is registered as part of the targets available to be run by
        fprime-util. Targets defined as both global and local are wrapped in delegating targets (one for each scope) and
        those delegates are registered. This is for brevity in definition of these targets. The flag "--all" is added to
        global targets to distinguish them

        Args:
            mnemonic:    mnemonic used to engage build targets. Is not unique, but mnemonic + flags must be.
            desc:        help description of this build target
            build_types: supported build types for target. Defaults to [BuildType.BUILD_NORMAL, BuildType.BUILD_TESTING]
            flags:       flags used to uniquely identify build targets who share logical mnemonics. Defaults to None.
            cmake:       cmake target override to handle oddly named cmake targets
        """
        self.mnemonic = mnemonic
        self.desc = desc
        self.build_type = (
            build_type if build_type is not None else BuildType.BUILD_NORMAL
        )
        self.flags = flags if flags is not None else set()
        self.scope = scope

        # Targets defined as either local or global scope are registered directly. "Both" targets are wrapped in a
        # delegator for both scopes and those end up being registered.
        if self.scope != TargetScope.BOTH:
            self.ALL_TARGETS.append(self) # Add newly minted target to the tracked list of targets
        else:
            DelegatorTarget(self, mnemonic, desc, TargetScope.LOCAL, build_type, flags)
            new_flags = {"-all"}
            new_flags = new_flags.union(flags) if flags else new_flags
            DelegatorTarget(self, mnemonic, desc, TargetScope.GLOBAL, build_type, new_flags)


    def __str__(self):
        """Makes this target into a string"""
        return self.config_string(self.mnemonic, self.flags)

    @staticmethod
    def config_string(mnemonic, flags):
        """Converts a mnemonic and set of flags to string

        Args:
            mnemonic: mnemonic of the target
            flags: sset of flags to pair with mnemonic
        Returns:
            string of format "mnemonic --flag1 --flag2 ..."
        """
        flag_string = " ".join(["--{}".format(flag) for flag in flags])
        flag_string = "" if flag_string == "" else " " + flag_string
        return "{}{}".format(mnemonic, flag_string)

    @classmethod
    def get_all_possible_flags(cls) -> Set[str]:
        """Gets list of all targets' flags used

        Returns:
            List of targets's supported by the system
        """
        return functools.reduce(
            lambda agg, item: agg.union(item.flags), cls.get_all_targets(), set()
        )

    @classmethod
    def get_all_targets(cls) -> List["Target"]:
        """Gets list of all targets registered

        Returns:
            List of targets supported by the system
        """
        return cls.ALL_TARGETS

    @classmethod
    def get_target(cls, mnemonic: str, flags: Set[str]) -> "Target":
        """Gets the actual build target given the parsed namespace

        Using the global list of build targets and the flags supplied to the namespace, attempt to determine which build
        targets can be used. If more than one are found, then generate exception.

        Args:
            mnemonic: mnemonic of command to look for
            flags:    flags to narrow down target

        Returns:
            single matching target
        """
        matching = []
        for target in cls.get_all_targets():
            if target.mnemonic == mnemonic and flags == target.flags:
                matching.append(target)
        if not matching:
            raise NoSuchTargetException(
                "Could not find target '{}'".format(cls.config_string(mnemonic, flags))
            )
        assert len(matching) == 1, "Conflicting targets specified in code"
        return matching[0]


class CompositeTarget(Target):
    """ Target whose execution is a composition of other targets """

    def __init__(self, targets, *args, **kwargs):
        """ Constructor setting child targets """
        super().__init__(*args, **kwargs)
        self.targets = targets

    def execute(self, *args, **kwargs):
        """ Execute the composite target """
        for child in self.targets:
            child.execute(*args, **kwargs)


class BuildSystemTarget(Target):
    """ Target whose execution invokes a command within the build system """
    def __init__(self, build_target, *args, **kwargs):
        """ Constructor setting child targets """
        super().__init__(*args, **kwargs)
        self.build_target = build_target

    def execute(self, builder: "Builder", context: Path, args: dict):
        """Execute a build target

        Executes a target within the build system. This will execute the target by calling into the build system.
        Context is supplied such that the system can match local targets to the global target list.

        Args:
            target: target to run
            context: context path for local targets
            make_args: make system arguments directly supplied
        """
        # Global targets with build target "" must be mapped to "arg"
        build_target = self.build_target if self.build_target != "" or self.scope == TargetScope.LOCAL else "all"
        builder.execute_build_target(build_target, context, self.scope == TargetScope.GLOBAL, args)


class DelegatorTarget(Target):
    """ Delegates to another target

    Sometimes a target needs to be created that delegates to another target. As an example, local and global variants of
    a "both" target need to delegate through the original. This target delegates to other targets.
    """
    def __init__(self, delegate: Target, *args, **kwargs):
        """ Constructor """
        super().__init__(*args, **kwargs)
        self.delegate = delegate

    def execute(self, *args, **kwargs):
        """ Delegate the execution """
        old_scope = self.delegate.scope
        try:
            # Temporarily overrides effective scope of delegate for this invocation
            self.delegate.scope = self.scope
            return_value = self.delegate.execute(*args, **kwargs)
        finally:
            self.delegate.scope = old_scope
        return return_value