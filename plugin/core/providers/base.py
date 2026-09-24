from abc import ABC,abstractmethod
class AIProvider(ABC):
    id="base"
    @abstractmethod
    def list_models(self): raise NotImplementedError
    @abstractmethod
    def chat(self,messages,model): raise NotImplementedError
