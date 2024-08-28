from django.core.signing import JSONSerializer
from langchain_core.vectorstores.base import VectorStoreRetriever
import pickle, sys, io

class Serializer():
    
    def dumps(self, obj):
        if type(obj) is VectorStoreRetriever:
            return pickle.dumps(obj)
        return JSONSerializer().dumps(obj)

    def loads(self, data):
        try:
            return JSONSerializer().loads(data)
        except TypeError:
            return self.VectorUnpickler(io.BytesIO(data)).load()

class VectorUnpickler(pickle.Unpickler):

    def find_class(self, module, name):
        if module == "langchain_core.vectorstores.base" and name == "VectorStoreRetriever":
            return getattr(sys.modules[module], name)
        raise pickle.UnpicklingError("Attempted to unpickle an unrecognized class")